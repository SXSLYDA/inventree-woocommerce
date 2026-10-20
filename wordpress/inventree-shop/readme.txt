=== InvenTree Shop ===
Contributors: omgharness
Tags: woocommerce, inventree, trade account, credit account, purchase order
Requires at least: 6.4
Tested up to: 6.9
Requires PHP: 7.4
Stable tag: 0.4.1
License: MIT

Quote requests and "charge to my account" for shops run from InvenTree.

== Description ==

Companion to the inventree-woocommerce InvenTree plugin (https://github.com/SXSLYDA/inventree-woocommerce).

* Quote requests for logged-in customers: as many items as they need, each with layout drawings / photos,
  a sample (with a printable sample label) or a call to discuss. Requests go to InvenTree; if it can't be reached
  they're queued and retried. Customers see and accept quotes (all or some lines) under My Account > Quotes, then
  pay online or charge their trade account. Emails for received, ready, sample received, declined, expiring and
  expired quotes.
* Quote PDFs (printed by InvenTree, or by the system that priced the quote, e.g. Xero): attached to the
  "your quote is ready" email and downloadable from My Account > Quotes.
* Quotes can carry labour, freight and other charges, and can be accepted line by line or - when priced in a
  system that only takes whole quotes, e.g. Xero - as a whole.
* A "Request a custom version" button on product pages.
* Adds a "Charge to my account" payment method, shown only to logged-in customers that InvenTree has approved
  for credit, and only when the order fits their available credit.
* Optional (or required) purchase order number at checkout, shown on the order and in order emails.
* Works with the classic checkout and the Cart & Checkout blocks, and with High-Performance Order Storage.
* Shows the customer's trade account and available credit on their My Account dashboard.

Approval and credit are set from InvenTree (company parameters "Shop Account" and "Credit Limit") and written
to each customer's login by the InvenTree plugin. Nothing is configured per customer in WordPress.

== Installation ==

1. Upload the plugin folder and activate it (WooCommerce must be active).
2. WooCommerce > InvenTree Shop: InvenTree address, shop link secret (same as in InvenTree), quote request page
   (a page with the [inventree_quote_request] shortcode), sample address and phone.
3. WooCommerce > Settings > Payments > Charge to account (InvenTree): enable, choose the PO number option and
   the order status.
4. Set up the InvenTree side as described in the inventree-woocommerce README.
