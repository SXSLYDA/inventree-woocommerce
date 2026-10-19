/**
 * InvenTree UI panels for the WooCommerce plugin.
 *
 * Plain ES module, no build step: React and Mantine come from the globals
 * InvenTree's own UI provides (window.React, window.MantineCore,
 * window.MantineNotifications) - the same libraries plugin bundlers mark as
 * external. Each export is a one-argument entry point InvenTree calls with
 * its plugin context, returning a React element.
 *
 *   renderShopDashboard  - dashboard widget (staff)
 *   renderShopPartPanel  - "Web Shop" panel on a part
 *   renderShopOrderPanel - "Web Order" panel on a Sales Order
 */

const React = window.React;
const M = window.MantineCore;
const h = React.createElement;
const { useState, useEffect, useCallback } = React;

const BASE = '/plugin/woocommerce';

// ---------------------------------------------------------------- helpers

function notify(color, title, message) {
  const n = window.MantineNotifications?.notifications;
  if (n) n.show({ color, title, message });
}

function errorText(err) {
  const data = err?.response?.data;
  return data?.detail || data?.error || err?.message || 'Something went wrong.';
}

function when(value) {
  if (!value) return 'never';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

/** GET a plugin endpoint, with loading / error state and a reload function. */
function useEndpoint(context, path) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    context.api
      .get(`${BASE}${path}`)
      .then((r) => setState({ loading: false, data: r.data, error: null }))
      .catch((e) => setState({ loading: false, data: null, error: errorText(e) }));
  }, [context.api, path]);
  useEffect(load, [load]);
  return [state, load];
}

/** A button that POSTs to an endpoint and reports the result. */
function ActionButton({ context, path, label, done, onDone, variant, color, size, timeout }) {
  const [busy, setBusy] = useState(false);
  const run = () => {
    setBusy(true);
    context.api
      .post(`${BASE}${path}`, {}, { timeout: timeout || 120000 })
      .then((r) => {
        const err = r.data?.error;
        if (err) notify('orange', label, err);
        else notify('green', label, done || r.data?.detail || 'Done');
        onDone?.(r.data);
      })
      .catch((e) => notify('red', label, errorText(e)))
      .finally(() => setBusy(false));
  };
  return h(M.Button, { onClick: run, loading: busy, variant: variant || 'light', color, size: size || 'xs' }, label);
}

function InternalLink({ context, to, children }) {
  return h(
    M.Anchor,
    {
      href: to,
      size: 'sm',
      onClick: (e) => {
        if (e.ctrlKey || e.metaKey || e.button === 1) return;
        e.preventDefault();
        context.navigate(to);
      },
    },
    children,
  );
}

function ExternalLink({ href, children }) {
  if (!href) return null;
  return h(M.Anchor, { href, target: '_blank', rel: 'noopener noreferrer', size: 'sm' }, children);
}

function Rows({ rows }) {
  const shown = rows.filter((r) => r && r[1] !== null && r[1] !== undefined && r[1] !== '');
  return h(
    M.Table,
    { withRowBorders: false, verticalSpacing: 4, horizontalSpacing: 'xs' },
    h(
      M.Table.Tbody,
      null,
      shown.map(([label, value]) =>
        h(
          M.Table.Tr,
          { key: label },
          h(M.Table.Td, { style: { width: '35%' } }, h(M.Text, { size: 'sm', c: 'dimmed' }, label)),
          h(M.Table.Td, null, typeof value === 'string' || typeof value === 'number' ? h(M.Text, { size: 'sm' }, value) : value),
        ),
      ),
    ),
  );
}

function Loading({ state, children }) {
  if (state.loading && !state.data) return h(M.Group, { justify: 'center', p: 'md' }, h(M.Loader, { size: 'sm' }));
  if (state.error) return h(M.Alert, { color: 'red', title: 'Could not load' }, state.error);
  return children();
}

const MODE_LABEL = { stock: 'Sold from stock', build: 'Built to order' };
const ORDER_STATE_COLOR = { created: 'green', queued: 'blue', received: 'gray', failed: 'red' };

// ---------------------------------------------------------------- part panel

function PartPanel({ context }) {
  const [state, reload] = useEndpoint(context, `/part/${context.id}/`);
  return h(Loading, { state }, () => {
    const d = state.data;
    const product = d.product;
    let badge;
    if (d.in_shop) badge = h(M.Badge, { color: d.draft_no_price ? 'yellow' : 'green' }, d.draft_no_price ? 'In the shop (draft)' : 'In the shop');
    else if (d.held) badge = h(M.Badge, { color: 'orange' }, 'Waiting for release');
    else badge = h(M.Badge, { color: 'gray' }, 'Not in the shop');

    const docs = d.documents.length
      ? h(
          M.Stack,
          { gap: 2 },
          d.documents.map((doc) => h(ExternalLink, { key: doc.url, href: doc.url }, doc.label)),
        )
      : h(M.Text, { size: 'sm', c: 'dimmed' }, 'None. Tag an attachment "shop" (or put [shop] in its comment) to list it on the product page.');

    return h(
      M.Stack,
      { gap: 'sm' },
      h(
        M.Group,
        { justify: 'space-between' },
        h(M.Group, { gap: 'xs' }, badge, d.mode ? h(M.Badge, { variant: 'light' }, MODE_LABEL[d.mode] || d.mode) : null),
        d.can_manage && (d.in_shop || product)
          ? h(ActionButton, { context, path: `/part/${context.id}/`, label: 'Send to shop now', done: 'Listing sent', onDone: reload })
          : null,
      ),
      d.reason ? h(M.Alert, { color: d.held ? 'orange' : 'gray' }, d.reason) : null,
      d.draft_no_price ? h(M.Alert, { color: 'yellow' }, 'No sale price - listed as a draft so it can\'t be bought. Add a sell price break.') : null,
      product?.error ? h(M.Alert, { color: 'red', title: 'Last sync problem' }, product.error) : null,
      h(Rows, {
        rows: [
          ['Price', d.price ? `${d.price} ${d.currency}` : d.in_shop ? 'none' : null],
          ['Available to the shop', d.available],
          ['Revision', d.revision],
          ['Product page', product?.url ? h(ExternalLink, { href: product.url }, 'Open in shop') : null],
          ['WooCommerce', product?.admin_url ? h(ExternalLink, { href: product.admin_url }, `Edit product #${product.id}`) : null],
          ['Stock last sent', product ? product.stock_sent : null],
          ['Last synced', product ? when(product.last_synced) : null],
        ],
      }),
      h(M.Text, { size: 'sm', fw: 500 }, 'Documents on the product page'),
      docs,
    );
  });
}

// ---------------------------------------------------------------- sales order panel

function OrderPanel({ context }) {
  const [state] = useEndpoint(context, `/salesorder/${context.id}/`);
  return h(Loading, { state }, () => {
    const d = state.data;
    if (!d.found) return h(M.Text, { size: 'sm', c: 'dimmed' }, 'This Sales Order didn\'t come from the web shop.');
    return h(
      M.Stack,
      { gap: 'sm' },
      h(
        M.Group,
        { gap: 'xs' },
        h(M.Badge, { color: d.on_account ? 'grape' : 'green' }, d.on_account ? 'Charged to account' : 'Paid online'),
        d.held ? h(M.Badge, { color: 'red' }, 'Put on hold') : null,
        h(M.Badge, { variant: 'light' }, `WooCommerce: ${d.woo_status}`),
      ),
      d.warnings.length
        ? h(
            M.Alert,
            { color: d.held ? 'red' : 'yellow', title: 'Needs checking' },
            h(M.Stack, { gap: 2 }, d.warnings.map((w) => h(M.Text, { key: w, size: 'sm' }, w))),
          )
        : null,
      h(Rows, {
        rows: [
          ['Web order', d.admin_url ? h(ExternalLink, { href: d.admin_url }, `#${d.number}`) : `#${d.number}`],
          ['Customer PO', d.po_number],
          ['Payment', d.on_account ? 'On account - invoice the customer' : d.payment_method],
          ['Transaction', d.transaction_id],
          ['Order total', d.total ? `${d.total} ${d.currency} (tax ${d.total_tax})` : null],
          ['Customer note', d.customer_note],
          ['Received', when(d.received_at)],
          [
            'Build Orders',
            d.builds.length
              ? h(
                  M.Group,
                  { gap: 'xs' },
                  d.builds.map((b) => h(InternalLink, { key: b.pk, context, to: `/manufacturing/build-order/${b.pk}` }, b.reference)),
                )
              : null,
          ],
        ],
      }),
    );
  });
}

// ---------------------------------------------------------------- dashboard

function Stat({ label, value, color }) {
  return h(
    M.Paper,
    { withBorder: true, p: 'xs', radius: 'md' },
    h(M.Text, { size: 'xs', c: 'dimmed' }, label),
    h(M.Text, { size: 'lg', fw: 700, c: color }, String(value ?? '-')),
  );
}

function Dashboard({ context }) {
  const [state, reload] = useEndpoint(context, '/status/');
  return h(Loading, { state }, () => {
    const d = state.data;
    const failed = d.orders.filter((o) => o.state === 'failed');
    const attention = d.orders.filter((o) => o.held || o.warnings.length);
    const s = d.last_summary || {};
    return h(
      M.ScrollArea,
      { h: '100%', type: 'auto' },
      h(
        M.Stack,
        { gap: 'sm', p: 2 },
        d.problems.map((p) => h(M.Alert, { key: p, color: 'red', p: 'xs' }, p)),
        h(
          M.SimpleGrid,
          { cols: 4, spacing: 'xs' },
          h(Stat, { label: 'In the shop', value: d.products.published }),
          h(Stat, { label: 'Sync problems', value: d.products.with_errors, color: d.products.with_errors ? 'red' : undefined }),
          h(Stat, { label: 'Awaiting release', value: d.awaiting_release.length, color: d.awaiting_release.length ? 'orange' : undefined }),
          h(Stat, { label: 'Account logins', value: d.accounts.logins_approved }),
        ),
        h(
          M.Text,
          { size: 'xs', c: 'dimmed' },
          `Last sync ${when(d.last_sync)}` +
            (s.created !== undefined ? ` - ${s.created} added, ${s.updated} updated, ${s.unpublished} removed, ${s.errors} errors` : ''),
        ),
        h(
          M.Group,
          { gap: 'xs' },
          h(ActionButton, { context, path: '/sync/', label: 'Sync whole shop', onDone: () => setTimeout(reload, 3000) }),
          h(ActionButton, { context, path: '/accounts/sync/', label: 'Sync accounts' }),
          h(ActionButton, { context, path: '/test-connection/', label: 'Test connection', done: 'WooCommerce answered', variant: 'subtle' }),
          h(M.Button, { size: 'xs', variant: 'subtle', onClick: reload }, 'Refresh'),
        ),
        d.awaiting_release.length
          ? h(
              Section,
              { title: 'Waiting for release (validate the BOM to put on sale)' },
              d.awaiting_release.map((p) =>
                h(M.Group, { key: p.part_id, gap: 'xs' }, h(InternalLink, { context, to: `/part/${p.part_id}/` }, p.name), h(M.Text, { size: 'xs', c: 'dimmed' }, p.reason)),
              ),
            )
          : null,
        d.product_errors.length
          ? h(
              Section,
              { title: 'Sync problems' },
              d.product_errors.map((p) =>
                h(M.Group, { key: p.part_id, gap: 'xs', wrap: 'nowrap' }, h(InternalLink, { context, to: `/part/${p.part_id}/` }, p.name || `Part ${p.part_id}`), h(M.Text, { size: 'xs', c: 'red', lineClamp: 1 }, p.last_error)),
              ),
            )
          : null,
        failed.length
          ? h(
              Section,
              { title: 'Orders that failed' },
              failed.map((o) =>
                h(
                  M.Group,
                  { key: o.pk, gap: 'xs', wrap: 'nowrap' },
                  h(M.Text, { size: 'sm' }, `#${o.woo_order_number}`),
                  h(M.Text, { size: 'xs', c: 'red', lineClamp: 1, style: { flex: 1 } }, o.error),
                  h(ActionButton, { context, path: `/orders/${o.pk}/retry/`, label: 'Retry', onDone: () => setTimeout(reload, 3000) }),
                ),
              ),
            )
          : null,
        h(
          Section,
          { title: attention.length ? `Recent web orders (${attention.length} need checking)` : 'Recent web orders' },
          d.orders.length
            ? d.orders.slice(0, 10).map((o) =>
                h(
                  M.Group,
                  { key: o.pk, gap: 'xs', wrap: 'nowrap' },
                  h(M.Text, { size: 'sm', w: 70 }, `#${o.woo_order_number}`),
                  h(M.Badge, { size: 'xs', color: ORDER_STATE_COLOR[o.state] || 'gray' }, o.state),
                  o.sales_order_id ? h(InternalLink, { context, to: `/sales/sales-order/${o.sales_order_id}` }, o.sales_order_reference || 'Sales Order') : null,
                  o.on_account ? h(M.Badge, { size: 'xs', color: 'grape', variant: 'light' }, 'account') : null,
                  o.held ? h(M.Badge, { size: 'xs', color: 'red' }, 'on hold') : null,
                  o.warnings.length ? h(M.Text, { size: 'xs', c: 'orange', lineClamp: 1 }, o.warnings[0]) : null,
                ),
              )
            : h(M.Text, { size: 'sm', c: 'dimmed' }, 'No web orders yet.'),
        ),
      ),
    );
  });
}

function Section({ title, children }) {
  return h(M.Stack, { gap: 4 }, h(M.Text, { size: 'sm', fw: 600 }, title), children);
}

// ---------------------------------------------------------------- entry points

export function renderShopDashboard(context) {
  return h(Dashboard, { context });
}

export function renderShopPartPanel(context) {
  return h(PartPanel, { context, key: `part-${context.id}` });
}

export function renderShopOrderPanel(context) {
  return h(OrderPanel, { context, key: `so-${context.id}` });
}
