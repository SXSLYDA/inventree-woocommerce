# inventree-woocommerce

An [InvenTree](https://inventree.org) plugin that runs a WooCommerce shop from your InvenTree data.

- **Publishes parts** you flag (or a whole category) as WooCommerce products: title, SKU, price, description,
  a specification table of chosen parameters, and the part image.
- **Keeps stock in sync** as it changes. Parts are sold either *from stock only* (no backorders) or
  *built to order* (backorders allowed, with a lead time shown).
- **Turns paid orders into Sales Orders**: customer, contact, shipping address, line items, shipping and fees,
  with a note back on the WooCommerce order.
- **Creates Build Orders** for build-to-order items that stock can't cover, linked to the Sales Order.
- **Credit accounts**: customers you approve in InvenTree can "charge to my account" at checkout (with their PO
  number), within a credit limit, using the companion WordPress plugin in `wordpress/inventree-shop/`.
  Everyone else pays online through any WooCommerce gateway (Stripe, PayPal, ...).

InvenTree stays the source of truth. WooCommerce does what it's good at: storefront, cart, checkout, payment,
tax and shipping.

Tested with InvenTree 1.5.8 and 1.6.0 (minimum 1.5.0).

## How it decides what's in the shop

A part is published when it is **Active** and **Salable**, and either:

| Shop parameter (default name `Web Shop`) | Result |
|---|---|
| `Yes` / `Stock` | Sold from stock only - WooCommerce won't sell more than you have |
| `Build` / `Build to order` | Built to order - backorders allowed, lead time from the `Lead Time` parameter |
| `No` | Kept out of the shop, even inside the shop category |
| *(not set)* | Published only if the part is in the **Shop category** (or below it), using the category mode |

**Build-to-order assemblies wait for a released design.** You're selling something you haven't built yet, so
it only goes on sale once its BOM is **validated** in InvenTree (and, if you turn on *Build to order needs a
locked part*, once the part is **locked**). Edit the BOM later and InvenTree un-validates it, which takes the
product out of the shop until you validate it again. Parts waiting on this are listed under `awaiting_release`
in the status endpoint. If the part has a **revision** it's shown on the product page.

A part with no sale price is sent as a **draft**, so nothing is ever sold for $0. The price is the
lowest-quantity sell price break, converted to the shop currency with InvenTree's exchange rates if needed.

**Stock sent to the shop** = in stock − allocated to builds − still to ship on open sales orders − stock reserve.
Unallocated web orders count straight away, so the shop can't resell units it has just sold.

Anything else (another plugin, a script, a person) can control the shop just by setting the shop parameter,
price and image on a part. This plugin has no dependency on how those get set.

## Install

1. Install into InvenTree's environment, or from InvenTree's *Plugin Settings > Install Plugin*:
   ```
   pip install inventree-woocommerce
   ```
2. In InvenTree *Admin Center > Plugin Settings*, turn on **Enable URL integration**,
   **Enable event integration** and **Enable schedule integration**. Then activate **WooCommerce Shop**.
3. Create the plugin's tables. It ships normal Django migrations:
   ```
   invoke migrate        # or: invoke update
   ```
   (Package installer: `inventree run invoke migrate`.) Make sure InvenTree's background worker is running -
   syncs and orders are processed there.

## Connect WooCommerce

1. **API key.** In WordPress: *WooCommerce > Settings > Advanced > REST API > Add key*, permission
   **Read/Write**. Copy the consumer key and secret into the plugin settings, along with the **Shop URL**
   (`https://...`). WordPress permalinks must be on (anything except "Plain").
2. **Webhook.** *WooCommerce > Settings > Advanced > Webhooks > Add webhook*:
   - Status: Active
   - Topic: **Order created**, then add a second identical webhook with topic **Order updated**. The order
     usually becomes paid after it's created, so you need both. Duplicate deliveries are ignored.
   - Delivery URL: `https://<your-inventree>/plugin/woocommerce/webhook/order/`
   - Secret: a long random string - put the same value in the plugin's **Webhook secret**
3. **Images.** WooCommerce downloads part images from InvenTree through signed links, so InvenTree must be
   reachable from the shop over https. If InvenTree's site URL isn't the public address, set
   **Public InvenTree URL**.
4. Check the connection (as a staff user):
   ```
   POST /plugin/woocommerce/test-connection/
   ```
   It reports the WooCommerce version and the store currency. The store currency must match **Shop currency**.
5. Flag a part with `Web Shop = Yes`, give it a sale price, then run a full sync:
   ```
   POST /plugin/woocommerce/sync/          {}                   (whole shop)
   POST /plugin/woocommerce/sync/          {"part_ids": [12]}   (just these parts)
   ```

## Settings

| Setting | What it does |
|---|---|
| Shop URL, Consumer key/secret | WooCommerce REST API access (https only) |
| Webhook secret | Verifies order webhooks (HMAC-SHA256) |
| Shop parameter / Shop category / Shop category mode | What's published and how it's sold (see above) |
| Lead time parameter | Shown on build-to-order products |
| Parameters shown in the shop | Comma-separated parameter names for the product's specification table |
| Product title / Product SKU | How products are named. An existing product with the same SKU is **adopted**, not duplicated |
| Shop currency | Must match the store currency |
| Stock reserve | Units of every part held back from the shop |
| Send part images / Public InvenTree URL | Image sync |
| When a part leaves the shop | Draft, private, or move to trash |
| Automatic sync | Event-driven updates plus a full re-check every 15 minutes |
| Paid order statuses | Which statuses create a Sales Order (default `processing,completed`) |
| Customers / Shared customer | One InvenTree customer per shop customer (matched by email), or one shared customer |
| Issue Sales Orders automatically | Off = web orders arrive as **Pending** for you to check |
| Note the Sales Order on the shop order | Adds a private note with the InvenTree reference |
| Account parameter / Credit limit parameter | Company parameters that approve a credit account and cap it |
| Account payment methods | WooCommerce payment method ids that mean "charge to account" |
| Sync accounts to the shop | Keep approval and available credit up to date on shop logins |
| Build to order needs a validated BOM / a locked part | When a build-to-order design counts as released |
| Build Orders for build-to-order items | Shortfall only, full quantity, or off |

## Payments: online and on account

**Online payment** is plain WooCommerce: install and set up a gateway (WooCommerce Stripe Payments, PayPal, ...).
Paid orders arrive with status *processing* and become Sales Orders. Unpaid ones (a bank transfer waiting in
*on-hold*) wait until you mark them paid.

**Charge to account** needs the companion WordPress plugin, `wordpress/inventree-shop/`:

1. Zip the `inventree-shop` folder and upload it in *WordPress > Plugins > Add New > Upload*, then activate.
   It works with both the classic and the block (Cart & Checkout blocks) checkout.
2. *WooCommerce > Settings > Payments > Charge to account (InvenTree)*: enable it, choose whether to ask for
   a purchase order number (optional / required / off), and the status orders get (Processing, or On hold
   if you want to release each one yourself first).
3. In InvenTree, on the customer company, add parameters:
   - `Shop Account` = `Yes`
   - `Credit Limit` = e.g. `5000` (shop currency; leave it off for no limit)
4. Make sure each person who orders has a shop login whose email is the company's email or one of its
   **contacts'** emails. The plugin finds their logins hourly (or right away with `POST /accounts/sync/`) and
   stores on each login: approved yes/no, available credit, company name.

At checkout, "Charge to my account" is offered only to logged-in, approved customers whose order fits their
available credit. Available credit = credit limit − the value of the company's **open** Sales Orders.
Invoices waiting for payment are in your accounting system, not InvenTree, so either allow for them in the
limit or close Sales Orders only once they're paid.

When an account order arrives, the plugin checks again with InvenTree's current data. If the company isn't
approved (any more) or the order is over the limit, the Sales Order is still created but put **On Hold** with
the reason, so nothing ships by mistake and nothing is lost. The customer's PO number becomes the Sales Order's
customer reference. Account orders are filed under the account company even if the person typed a different
company name at checkout.

## Build Orders

For each line of a new Sales Order, free stock = in stock − allocated to builds − demand from other open sales
orders.

- **Build-to-order** part (must be an assembly) short of stock: a Build Order for the shortfall (or the full
  quantity, if you choose), linked to the Sales Order. Its target date comes from the part's `Lead Time`
  parameter ("2 weeks", "10 working days", "3-4 weeks" - the longer end - or a number of days).
- **Stock-only** part short of stock (two customers bought the last ones at once): a warning on the order.

Build Orders are only made from a released design. If the BOM was edited (or the part unlocked) after the
customer ordered, the order gets a warning instead, so you re-validate and create the build yourself.

In-progress builds aren't counted, so an order is never left without a build. Cancel the new one if an existing
build covers it. If InvenTree refuses a Build Order (e.g. you require validated or locked BOMs and this one
isn't), the order is still created and the reason goes in its warnings.

## Events for other plugins

After a web order is committed the plugin fires InvenTree plugin events (needs *Enable event integration*):

| Event | Arguments |
|---|---|
| `woocommerce.salesorder_created` | `id` (Sales Order), `woo_order_id`, `woo_order_number`, `on_account`, `held`, `build_ids` |
| `woocommerce.build_created` | `id` (Build Order), `sales_order`, `part`, `quantity`, `woo_order_id` |

Another plugin subscribes with `EventMixin`:

```python
def wants_process_event(self, event):
    return event == "woocommerce.build_created"

def process_event(self, event, *args, **kwargs):
    build = Build.objects.get(pk=kwargs["id"])
    ...  # e.g. pin the build to the released design revision
```

## What happens to an order

When a webhook arrives with a paid status, the plugin creates, in one transaction:

- the **customer** with a **contact**, and the **shipping address**. The customer is found by: the company this
  shop login ordered for before, a company with a contact using the billing email, a company with that email -
  or created
- a **Sales Order** with customer reference = the customer's PO number, or `WC-<order number>`; a link to the
  WooCommerce order; a note with the payment method, transaction id, coupons, customer note and the tax charged
- a **line item** per product, at the price actually paid (after discounts, ex tax)
- **extra lines** for shipping and fees
- **Build Orders** where needed (see above)

A product that isn't linked to an InvenTree part still goes on the order, as an extra line flagged for you to
fix. Every webhook delivery is stored, and an order is only ever turned into one Sales Order, whatever
WooCommerce retries. Failed orders are retried automatically (up to 5 times), or from the admin page.

## Endpoints

| Endpoint | Access | Purpose |
|---|---|---|
| `POST /plugin/woocommerce/webhook/order/` | WooCommerce (signed) | Order webhook |
| `GET /plugin/woocommerce/image/<part>/<sig>.<ext>` | Public, signed | Part image for WooCommerce |
| `GET /plugin/woocommerce/status/` | Staff | Config problems, last sync, product errors, recent orders |
| `POST /plugin/woocommerce/sync/` | Staff | Sync now |
| `POST /plugin/woocommerce/test-connection/` | Staff | Check the API key |
| `POST /plugin/woocommerce/orders/<id>/retry/` | Staff | Retry a failed order |
| `POST /plugin/woocommerce/accounts/sync/` | Staff | Refresh account approval/credit on shop logins now |
| `GET /plugin/woocommerce/accounts/<company id>/` | Staff | An account's approval, limit, credit used and logins |

Product links and received orders are also listed in InvenTree's Django admin, with "re-send" and "retry"
actions.

## Security notes

- API keys only go over https. Keys and the webhook secret are stored as protected plugin settings.
- The webhook rejects anything without a valid signature. It never trusts prices from anywhere but the
  signed order body.
- Image links are HMAC-signed per part and image file, and only work while the part is in the shop.
- Account approval and credit are only ever written by InvenTree (through the REST API key). The WordPress
  plugin checks them again when the order is placed, and InvenTree checks a third time when it arrives.

## Roadmap

- Custom quote requests with file uploads (with a companion WordPress plugin)
- Shipment and tracking pushed back to WooCommerce
- A status panel inside InvenTree

## Development

```
pip install -e . django djangorestframework pytest ruff
pytest -q && ruff check .
```

`tests/integration/check_inventree.py` is an end-to-end check to run inside a **throwaway** InvenTree
database. See the docstring at the top of that file.

`wordpress/tests/test-gateway.php` checks the account payment method inside a **throwaway** WordPress install
(with a small WooCommerce stand-in): `php wordpress/tests/test-gateway.php /path/to/wordpress`.

Releasing: bump `inventree_woocommerce/version.py`, then tag `vX.Y.Z`. CI publishes to PyPI.

## License

MIT
