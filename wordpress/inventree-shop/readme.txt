=== InvenTree Shop ===
Contributors: omgharness
Tags: woocommerce, inventree, trade account, credit account, purchase order
Requires at least: 6.4
Tested up to: 6.9
Requires PHP: 7.4
Stable tag: 0.2.0
License: MIT

"Charge to my account" for trade customers approved in InvenTree.

== Description ==

Companion to the inventree-woocommerce InvenTree plugin (https://github.com/SXSLYDA/inventree-woocommerce).

* Adds a "Charge to my account" payment method, shown only to logged-in customers that InvenTree has approved
  for credit, and only when the order fits their available credit.
* Optional (or required) purchase order number at checkout, shown on the order and in order emails.
* Works with the classic checkout and the Cart & Checkout blocks, and with High-Performance Order Storage.
* Shows the customer's trade account and available credit on their My Account dashboard.

Approval and credit are set from InvenTree (company parameters "Shop Account" and "Credit Limit") and written
to each customer's login by the InvenTree plugin. Nothing is configured per customer in WordPress.

== Installation ==

1. Upload the plugin folder and activate it (WooCommerce must be active).
2. WooCommerce > Settings > Payments > Charge to account (InvenTree): enable, choose the PO number option and
   the order status.
3. Set up the InvenTree side as described in the inventree-woocommerce README.
