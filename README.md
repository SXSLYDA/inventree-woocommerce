# inventree-woocommerce

An [InvenTree](https://inventree.org) plugin that runs a WooCommerce shop from your InvenTree data.

- **Publishes parts** you flag (or a whole category) as WooCommerce products: title, SKU, price, description,
  a specification table of chosen parameters, the part image, and **documents** (drawings, datasheets) from the
  part's attachments.
- **Keeps stock in sync** as it changes. Parts are sold either *from stock only* (no backorders) or
  *built to order* (backorders allowed, with a lead time shown).
- **Turns paid orders into Sales Orders**: customer, contact, shipping address, line items, shipping and fees,
  with a note back on the WooCommerce order.
- **Creates Build Orders** for build-to-order items that stock can't cover, linked to the Sales Order.
- **Quote requests**: logged-in customers ask for prices on as many items as they need, each with layout drawings
  or photos, a sample, or a call to discuss. You price them in InvenTree; they accept all or part of the quote
  online and pay (or charge their account). Needs the companion WordPress plugin.
- **Shipments back to the customer**: completing a shipment in InvenTree emails "dispatched" / "delivered on"
  (with tracking if any) and completes the WooCommerce order once everything has shipped.
- **Panels in InvenTree**: Web Shop and Quote Requests dashboard widgets, a Web Shop panel on each salable part (in the shop? if
  not, why not?), and a Web Order panel on Sales Orders that came from the shop.
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
| `Build` / `Build to order` | Built to order - backorders allowed, lead time from the `Lead Time` parameter, or the *Default lead time* setting (6 weeks) |
| `No` | Kept out of the shop, even inside the shop category |
| *(not set)* | Published only if the part is in the **Shop category** (or below it), using the category mode |

**Build-to-order assemblies wait for a released design.** You're selling something you haven't built yet, so
it only goes on sale once its BOM is **validated** in InvenTree (and, if you turn on *Build to order needs a
locked part*, once the part is **locked**). Edit the BOM later and InvenTree un-validates it, which takes the
product out of the shop until you validate it again. Parts waiting on this are listed under `awaiting_release`
in the status endpoint. If the part has a **revision** it's shown on the product page.
Every build-to-order product also shows the *Build-to-order note* setting, e.g. "Call 08 1234 5678 to discuss
your requirements."

**Documents.** Mark an attachment on the part for the shop by giving it the tag `shop`, or by putting `[shop]`
in its comment. It's listed under "Documents" on the product page, named after its comment (without the marker)
or its file name. External link attachments are listed as they are. Uploaded files are served through signed
links that only work while the attachment is still marked and the part is still in the shop. PDFs and images open
in the browser; any other file type downloads.

A part with no sale price is sent as a **draft**, so nothing is ever sold for $0. The price is the
lowest-quantity sell price break, converted to the shop currency with InvenTree's exchange rates if needed.

**Stock sent to the shop** = in stock − allocated to builds − still to ship on open sales orders − stock reserve.
Unallocated web orders count straight away, so the shop can't resell units it has just sold.

Anything else (another plugin, a script, a person) can control the shop just by setting the shop parameter,
price and image on a part. This plugin has no dependency on how those get set.

## Install

One version covers both halves: the InvenTree plugin and the WordPress plugin (`wordpress/inventree-shop`).
Each [GitHub release](https://github.com/SXSLYDA/inventree-woocommerce/releases) has the WordPress zip.

1. **InvenTree, package installer:** install the update script once, then let it install the plugin. It pins
   the plugin to the newest release tag, backs up the database, runs `invoke update`, restarts InvenTree and
   checks the plugin's migrations:
   ```bash
   sudo curl -fsSL -o /usr/local/bin/inventree-shop-update \
        https://raw.githubusercontent.com/SXSLYDA/inventree-woocommerce/main/tools/server-update.sh
   sudo chmod +x /usr/local/bin/inventree-shop-update
   sudo inventree-shop-update            # newest release; or: sudo inventree-shop-update v0.6.0
   ```
   **Docker:** add `inventree-woocommerce @ git+https://github.com/SXSLYDA/inventree-woocommerce.git@vX.Y.Z`
   (the newest release tag) to `plugins.txt` in your data volume, run `invoke update` and restart.
2. In InvenTree *Admin Center > Plugin Settings*, turn on **Enable URL integration**,
   **Enable event integration**, **Enable schedule integration** and **Enable interface integration**. Then
   activate **WooCommerce Shop**.
3. Create the plugin's tables. It ships normal Django migrations:
   ```
   invoke migrate        # or: invoke update
   ```
   (Package installer: `inventree run invoke migrate`.) Make sure InvenTree's background worker is running -
   syncs and orders are processed there.
4. **Website:** upload `inventree-shop-X.Y.Z.zip` from the same release in WordPress > Plugins > Add New >
   Upload Plugin (WooCommerce must be active).

**Updating later:** `sudo inventree-shop-update --check-only` shows what's installed, pinned and newest;
`sudo inventree-shop-update` updates to the newest release (or name one). Read the release's notes in
[CHANGELOG.md](CHANGELOG.md) first - they say when a release adds a migration or changes the WordPress plugin,
and upload the matching WordPress zip when it does. Rolling back: `sudo inventree-shop-update v0.6.0
--allow-downgrade` (only between versions with the same migrations, or restore the backup it made).

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
| Show documents on products / Document tag | Which attachments are listed as product downloads |
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
| Default lead time / Build-to-order note | Lead time when a part has none (default 6 weeks), and a note on build-to-order products |
| Tell the customer about shipments | Dispatched / delivered emails and completing the order |
| Shop link secret | Shared with the WordPress plugin; signs quote requests and notifications |
| Quotes valid for / Expiry reminder | Days a quote can be accepted (30), reminder days before expiry (3) |
| Largest quote upload / Most files / Upload file types | Quote upload limits |
| Quote PDF: your details / Quote PDF: terms | Printed on quote PDFs |
| Quote tax rate / Quote tax name | Tax added on the PDF (10%, GST) |
| Quote report template / Quote PDF for customers | Which template prints quotes; PDF in the email and My Account |

## Quote requests

Customers must be **logged in** (no anonymous requests). The flow:

1. **Request** (WordPress, the `[inventree_quote_request]` page, or "Request a custom version" on a product):
   any number of items - custom harness, copy of an existing harness, or component - each with quantity,
   description, OEM part number and machine. A harness item needs at least one of: **layout drawing(s) / photos**
   (uploaded), **a sample** (they get a printable sample label with the reference and your address), or **a call
   to discuss** (phone and best time). Plus needed-by date, delivery postcode and notes. The customer and you are
   both emailed. If InvenTree can't be reached the request is queued on the website and retried hourly - the
   customer still gets their reference straight away.
2. **Work it** (InvenTree dashboard > **Quote Requests**): mark samples received (the customer is emailed),
   record calls, then price each item with one or more lines - an InvenTree part (search), quantity, unit price
   ex tax, lead time (defaults to the default lead time) - or mark it **can't supply** with a reason. **Add charge**
   adds a line with no part (labour, freight, a call-out fee): a fee on the WooCommerce order and an extra line on
   the Sales Order. Build-to-order
   assemblies must be released (BOM validated) before you can send them, same rule as the shop.
3. **Send**: the quote gets a validity (default 30 days) and the customer is emailed. A part that isn't in the
   shop gets a **hidden, quote-only** WooCommerce product: never shown or buyable in the catalogue, only payable
   through its quote's order.
4. **Accept** (My Account > Quotes): the customer unticks lines they don't want and accepts. InvenTree creates a
   pending-payment order with exactly those lines at the quoted prices (prices never come from the browser) and
   sends them to pay - online, or "Charge to my account" for approved accounts.
5. **Paid**: the normal order flow - Sales Order (customer reference = their PO, else the quote reference),
   Build Orders with the quoted lead times - and the quote is marked accepted, with the customer's drawings
   copied onto the Sales Order as attachments.
6. **Expiry**: a reminder a few days before (default 3), then the quote expires and any unpaid order is
   cancelled. You can also reopen a sent quote to change it, or decline it with a reason.

**Quote PDFs.** Quotes print through InvenTree's own report engine: a quote request is a report model (type
*quote request*), so the PDF is an ordinary report template you can edit in *Admin Center > Reports*. The plugin
installs **"Quote (web shop)"** the first time a PDF is needed: your InvenTree logo, *Quote PDF: your details*
(name, ABN, address, phone - one per line), the customer, date / valid until / required by, the priced lines
with lead times, subtotal ex GST, GST (*Quote tax rate*, default 10%) and total, any items you can't supply,
the customer's notes and *Quote PDF: terms*. Pick a different template with *Quote report template*.

- Staff: **Preview PDF (draft)** / **Download PDF** in the quote window (marked DRAFT until the quote is sent).
- Customers (*Quote PDF for customers*, on by default): the PDF is attached to the "your quote is ready" email
  and can be downloaded from My Account > Quotes. Only sent (or accepted / expired) quotes, and only their own.

Uploads: file types and sizes are settings (default 20 files, 20 MB each, PDFs/images/DXF/DWG/Office/CSV/text).
Files are stored privately and only staff can download them. Make sure the web servers allow uploads that big:
nginx `client_max_body_size` on InvenTree, and PHP `upload_max_filesize` / `post_max_size` on the website.

The website and InvenTree talk to each other with the **Shop link secret** (set the same value on both sides):
every call is HMAC-signed with a timestamp, and a quote's files are covered by SHA-256 hashes in the signed
payload, so nothing can be added or changed in transit.

### Pricing in another system (Xero, or your own plugin)

Quotes can be priced somewhere else - an accounting system, an ERP - by any InvenTree plugin that offers itself
as the **quote pricing provider**. With [inventree-xero](https://github.com/SXSLYDA/inventree-xero) 1.5+ (*Web shop
quote requests* = *Price and send in Xero*), each request becomes a draft Xero quote; you price it in Xero (parts,
labour, freight, discounts, your own tax rates) and mark it Sent, and the shop sends the customer **Xero's PDF**.
The shop does everything else as above: emails, My Account, acceptance, payment, the Sales Order, expiry.

While a provider is active:
- the Quote Requests window shows **Priced in Xero** with its quote number and a link; adding lines, sending and
  reopening are done there (samples, calls, notes, can't-supply and declining still work here);
- customers get the provider's PDF (the InvenTree template is the fallback);
- customers accept the **whole quote** unless the provider allows line-by-line acceptance.

*Where quotes are priced* = *Always in InvenTree* ignores providers. With no provider installed - or the provider
not ready (inventree-xero in a dry run or disconnected) - everything works as above.

**Writing a provider.** No import of this plugin is needed; implement any of these on your plugin class
(full contract in [`providers.py`](inventree_woocommerce/providers.py)):

```python
def woocommerce_quote_provider(self):          # required: None = not pricing quotes right now
    return {"name": "MyBooks", "partial_accept": False, "pdf": True}

def woocommerce_quote_info(self, quote):       # optional: shown to staff
    return {"number": "Q-0042", "status": "Draft", "url": "https://...", "error": ""}

def woocommerce_quote_pdf(self, quote):        # optional: (bytes, file name) or None
    return pdf_bytes, "Q-0042.pdf"
```

Then drive the quote through `inventree_woocommerce.quotes.apply_action(quote, data, load_config(),
source="provider")` - `{"action": "set_lines", "lines": [...]}` (rows with `part_id`, or no `part_id` and a
`description` for a charge; `quantity`, `unit_price` ex tax, optional `item_id`, `lead_time`), `"send"`
(optional `valid_days`), `"reopen"`, `"decline"` (with `reason`). Listen for `woocommerce.quote_received` and
`woocommerce.quote_accepted` (below) to create and close the quote on your side. inventree-xero's `shop_quotes.py`
is a complete example.

## Shipments

When a web order's **Shipment** is completed in InvenTree, the customer gets a WooCommerce customer note (emailed):
"Your order has been dispatched (shipment 1 of 2)", with the tracking number and link if the shipment has them -
or "ready for collection" for a Local pickup order without tracking. Set the shipment's delivery date and they
get "delivered on ...". When everything has shipped (or the Sales Order is completed) the WooCommerce order is
set to Completed. Each message is sent once, however many times the shipment is saved.

Rates at checkout are WooCommerce's: e.g. a Local delivery method with postcode zones, plus Local pickup; add a
carrier plugin later if you post parcels.

## Panels in InvenTree

- **Dashboard → Web Shop** (staff): setup problems, products in the shop, sync problems, parts awaiting release,
  approved account logins, last sync, failed orders with **Retry**, and recent web orders (account / on hold /
  warnings, linked to their Sales Orders). Buttons: **Sync whole shop**, **Sync accounts**, **Test connection**.
  Add it from the dashboard's widget list.
- **Part → Web Shop** (salable parts): in the shop or not, and *why not* (inactive, not salable, not flagged,
  shop parameter says No, waiting for BOM validation); sold from stock or built to order; price, stock offered,
  revision, product page and WooCommerce edit links, last sync and any error, and the documents listed.
  Staff get **Send to shop now**.
- **Sales Order → Web Order** (orders from the shop): paid online or charged to account, on hold, warnings,
  customer PO, payment and transaction, totals, customer note, and its Build Orders.

## Payments: online and on account

**Online payment** is plain WooCommerce: install and set up a gateway (WooCommerce Stripe Payments, PayPal, ...).
Paid orders arrive with status *processing* and become Sales Orders. Unpaid ones (a bank transfer waiting in
*on-hold*) wait until you mark them paid.

**Charge to account** (and quote requests) need the companion WordPress plugin, `wordpress/inventree-shop/`:

1. Zip the `inventree-shop` folder and upload it in *WordPress > Plugins > Add New > Upload*, then activate.
   It works with both the classic and the block (Cart & Checkout blocks) checkout.
   Then *WooCommerce > InvenTree Shop*: InvenTree address, **Shop link secret** (same as in InvenTree), the quote
   request page (create a page containing `[inventree_quote_request]`), who gets new-request emails, your phone,
   and the address samples are sent to.
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
available credit. Available credit = credit limit − what the account owes:

- the value of the company's **open** Sales Orders charged to account or entered by hand (web orders paid
  online use no credit), and
- with the [inventree-xero](https://github.com/SXSLYDA/inventree-xero) plugin installed, the customer's **unpaid
  Xero invoices**. An order whose Xero invoice is approved is then counted once, from Xero, not twice. Credit on
  shop logins is refreshed whenever the Xero plugin reports new balances (event `xero.balances_updated`).
  Turn this off with *Count unpaid Xero invoices*.

Without the Xero plugin, invoices waiting for payment aren't visible to InvenTree, so either allow for them in
the limit or close Sales Orders only once they're paid.

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
| `woocommerce.quote_received` | `id` (Quote Request), `reference` |
| `woocommerce.quote_accepted` | `id` (Quote Request), `reference`, `sales_order` |

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
| `GET /plugin/woocommerce/file/<attachment>/<sig>/<name>` | Public, signed | Product document |
| `GET /plugin/woocommerce/part/<part>/` | Signed in | Part's shop status (the part panel) |
| `POST /plugin/woocommerce/part/<part>/` | Staff | Re-send one part now |
| `GET /plugin/woocommerce/salesorder/<so>/` | Signed in | Web order details (the Sales Order panel) |
| `POST /plugin/woocommerce/quotes/submit/` | WordPress (signed) | New quote request (payload + files) |
| `POST /plugin/woocommerce/quotes/customer/` | WordPress (signed) | A customer's quotes, for My Account |
| `POST /plugin/woocommerce/quotes/accept/` | WordPress (signed) | Accept lines, get the payment link |
| `POST /plugin/woocommerce/quotes/pdf/` | WordPress (signed) | PDF of a customer's sent quote |
| `GET /plugin/woocommerce/quotes/<id>/pdf/` | Staff | Quote PDF (draft until sent) |
| `GET/POST /plugin/woocommerce/quotes/...` | Staff | Quote Requests widget (list, detail, actions, files) |
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
- Image and document links are HMAC-signed per file, and only work while the part is in the shop (and, for
  documents, while the attachment is still marked for the shop). Only PDFs and plain images are shown inline;
  everything else is sent as a download with a sandboxing Content-Security-Policy, so an HTML or SVG attachment
  can't run in InvenTree's domain.
- Account approval and credit are only ever written by InvenTree (through the REST API key). The WordPress
  plugin checks them again when the order is placed, and InvenTree checks a third time when it arrives.

## Roadmap

- Product weights and sizes from InvenTree parameters, for carrier rates at checkout
- Tracking through WooCommerce's order fulfillments (10.2+) when it leaves beta

## Development

```
pip install -e . django djangorestframework pytest ruff
pytest -q tests && ruff check .
python tools/gen_settings_doc.py        # after changing settings: rewrites docs/SETTINGS.md
```

`tests/live/run_live.sh` is what CI runs for the real thing: a fresh InvenTree database with the plugin,
`tests/integration/check_inventree.py` (every InvenTree-side flow), then a real WordPress from GitHub (SQLite)
with the inventree-shop plugin talking to InvenTree's dev server (`wordpress/tests/test-gateway.php`,
`test-quotes.php`):

```
tests/live/run_live.sh /path/to/InvenTree /path/to/venv/bin/python /tmp/shop-live   # [--no-wordpress]
```

`tests/frontend/render_panels.mjs` renders the InvenTree panels with React 19 and Mantine 9 in jsdom
(see the comment at the top for the npm packages it needs). The panels are a single plain JavaScript file,
`inventree_woocommerce/static/shop_panels.js`, with no build step.

**Releasing** is `build-and-bump.ps1` (Windows PowerShell or PowerShell 7): checks, tests, bumps both plugins'
version, stamps the changelog, builds the WordPress zip, commits and tags `vX.Y.Z`. CI then tests the tag and
publishes a GitHub release with the WordPress zip. See [docs/RELEASING.md](docs/RELEASING.md).

## License

MIT
