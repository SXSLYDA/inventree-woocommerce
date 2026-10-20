# Changelog

One version covers both halves: the InvenTree plugin (`inventree-woocommerce`) and the WordPress plugin (`wordpress/inventree-shop`). Install the same version on both sides.

Add notes for the next release under **Unreleased** as you work. `build-and-bump.ps1` turns that heading into the version number and date when you release.

## Unreleased

## 0.6.0 — 2026-10-21

- **Quote pricing providers:** another InvenTree plugin can own quote pricing (`providers.py`, three duck-typed hooks). With inventree-xero 1.5+ quotes are priced and sent in Xero, customers get Xero's PDF and accept the whole quote. Setting *Where quotes are priced* (`QUOTE_PRICING`).
- **Charges on quotes:** lines with no part (labour, freight, call-out fees) - WooCommerce fees on the order, extra lines on the Sales Order. **Add charge** in the Quote Requests window.
- While a provider prices quotes, the Quote Requests window shows "Priced in ..." with its number and link, and setting lines, sending and reopening happen there.
- WordPress plugin: no line tick-boxes when only the whole quote can be accepted; the "quote ready" email says so.
- **Migration** `0005_quote_line_charges` (quote line part becomes optional).
- The WordPress plugin now carries the same version number as the InvenTree plugin (was 0.4.1).
- Release tooling: `build-and-bump.ps1`, `tools/server-update.sh`, CI release checks, a GitHub release with the WordPress zip, optional deploy. `docs/SETTINGS.md` generated from the code.

## 0.5.0 — 2026-10-20

- **Quote PDFs** through InvenTree's report engine: a quote request is a report model, with a default template "Quote (web shop)" (logo, your details, lines, GST, terms). Staff preview/download; customers get it with the "quote ready" email and in My Account.

## 0.4.1 — 2026-10-20

- Credit used includes unpaid Xero invoices when inventree-xero is installed (*Count unpaid Xero invoices*); refreshed on `xero.balances_updated`. Paid-online web orders use no credit.

## 0.4.0 — 2026-10-19

- **Quote requests** for logged-in customers: itemised, with drawings, samples (printable label) or a call; priced in InvenTree, accepted and paid online or on account; reminders and expiry. Migration `0004_quotes_and_shipments`.
- **Shipment updates:** dispatched / delivered notes to the customer, order completed in WooCommerce.
- Default lead time and "call to discuss" note for build-to-order parts.

## 0.3.0 — 2026-10-19

- Product documents from InvenTree attachments; InvenTree panels (shop dashboard, part and sales order panels). Migration `0003_product_permalink`.

## 0.2.1 — 2026-10-19

- Build-to-order parts are released by InvenTree BOM validation (optionally locked parts).

## 0.2.0 — 2026-10-19

- Credit accounts ("Charge to my account", WordPress plugin), Build Orders for build-to-order lines, events for other plugins. Migration `0002_accounts_and_builds`.

## 0.1.0 — 2026-10-19

- Catalogue and stock sync from InvenTree to WooCommerce; paid orders become Sales Orders (signed webhook).
