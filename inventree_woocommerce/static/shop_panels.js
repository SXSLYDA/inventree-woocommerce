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
 *   renderQuoteDashboard - "Quote Requests" dashboard widget (staff): list, and a
 *                          full-size window to work each request
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

// ---------------------------------------------------------------- quote requests

const QUOTE_COLOR = {
  new: 'blue', waiting_sample: 'orange', call: 'grape', reviewing: 'cyan', quoted: 'teal',
  accepted: 'green', declined: 'red', expired: 'gray', cancelled: 'gray',
};
const OPEN_STATUSES = ['new', 'waiting_sample', 'call', 'reviewing'];

function day(value) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleDateString();
}

function QuoteDashboard({ context }) {
  const [filter, setFilter] = useState('open');
  const [state, reload] = useEndpoint(context, `/quotes/?status=${filter}`);
  const [openId, setOpenId] = useState(null);
  return h(
    M.Stack,
    { gap: 'xs', h: '100%' },
    h(
      M.Group,
      { justify: 'space-between' },
      h(M.SegmentedControl, {
        size: 'xs', value: filter, onChange: setFilter,
        data: [
          { value: 'open', label: 'Open' }, { value: 'accepted', label: 'Accepted' },
          { value: 'declined', label: 'Declined' }, { value: 'expired', label: 'Expired' }, { value: 'all', label: 'All' },
        ],
      }),
      h(M.Button, { size: 'xs', variant: 'subtle', onClick: reload }, 'Refresh'),
    ),
    h(Loading, { state }, () => {
      const rows = state.data.quotes;
      if (!rows.length) return h(M.Text, { size: 'sm', c: 'dimmed' }, 'No quote requests here.');
      return h(
        M.ScrollArea,
        { type: 'auto', style: { flex: 1 } },
        h(
          M.Table,
          { highlightOnHover: true, verticalSpacing: 4, fz: 'sm' },
          h(M.Table.Thead, null, h(M.Table.Tr, null, ['Reference', 'Status', 'Customer', 'Items', 'Waiting on', 'Received', 'Needed by'].map((c) => h(M.Table.Th, { key: c }, c)))),
          h(
            M.Table.Tbody,
            null,
            rows.map((q) =>
              h(
                M.Table.Tr,
                { key: q.id, style: { cursor: 'pointer' }, onClick: () => setOpenId(q.id) },
                h(M.Table.Td, null, h(M.Text, { size: 'sm', fw: 600 }, q.reference)),
                h(M.Table.Td, null, h(M.Badge, { size: 'sm', color: QUOTE_COLOR[q.status] || 'gray' }, q.status_label)),
                h(M.Table.Td, null, [q.name, q.company_name].filter(Boolean).join(' - ')),
                h(M.Table.Td, null, String(q.items)),
                h(
                  M.Table.Td,
                  null,
                  h(M.Group, { gap: 4 },
                    q.samples_due ? h(M.Badge, { size: 'xs', color: 'orange', variant: 'light' }, `${q.samples_due} sample${q.samples_due > 1 ? 's' : ''}`) : null,
                    q.calls_due ? h(M.Badge, { size: 'xs', color: 'grape', variant: 'light' }, `${q.calls_due} call${q.calls_due > 1 ? 's' : ''}`) : null),
                ),
                h(M.Table.Td, null, day(q.created_at)),
                h(M.Table.Td, null, day(q.required_by)),
              ),
            ),
          ),
        ),
      );
    }),
    h(
      M.Modal,
      { opened: openId !== null, onClose: () => { setOpenId(null); reload(); }, size: '90%', title: 'Quote request' },
      openId !== null ? h(QuoteDetail, { context, id: openId, key: openId }) : null,
    ),
  );
}

function QuoteDetail({ context, id }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    context.api
      .get(`${BASE}/quotes/${id}/`)
      .then((r) => setState({ loading: false, data: r.data, error: null }))
      .catch((e) => setState({ loading: false, data: null, error: errorText(e) }));
  }, [context.api, id]);
  useEffect(load, [load]);

  const act = (data, okColor) => {
    setBusy(true);
    return context.api
      .post(`${BASE}/quotes/${id}/`, data, { timeout: 120000 })
      .then((r) => {
        notify(okColor || 'green', 'Quote', r.data.detail);
        setState({ loading: false, data: r.data.quote, error: null });
        return true;
      })
      .catch((e) => { notify('red', 'Quote', errorText(e)); return false; })
      .finally(() => setBusy(false));
  };

  return h(Loading, { state }, () => {
    const q = state.data;
    const editable = OPEN_STATUSES.includes(q.status);
    return h(
      M.Stack,
      { gap: 'md' },
      h(
        M.Group,
        { justify: 'space-between', align: 'flex-start' },
        h(
          M.Stack,
          { gap: 2 },
          h(M.Group, { gap: 'xs' }, h(M.Title, { order: 3 }, q.reference), h(M.Badge, { color: QUOTE_COLOR[q.status] || 'gray' }, q.status_label)),
          h(M.Text, { size: 'sm' }, [q.name, q.company_name].filter(Boolean).join(' - ')),
          h(M.Group, { gap: 'md' },
            q.email ? h(ExternalLink, { href: `mailto:${q.email}` }, q.email) : null,
            q.phone ? h(ExternalLink, { href: `tel:${q.phone.replace(/\s+/g, '')}` }, q.phone) : null),
        ),
        h(Rows, {
          rows: [
            ['Received', when(q.created_at)],
            ['Needed by', day(q.required_by)],
            ['Delivery postcode', q.delivery_postcode],
            ['Valid until', day(q.valid_until)],
            ['Sales Order', q.sales_order_id ? h(InternalLink, { context, to: `/sales/sales-order/${q.sales_order_id}` }, 'Open') : null],
          ],
        }),
      ),
      q.notes ? h(M.Alert, { color: 'gray', title: 'Customer notes' }, q.notes) : null,
      q.decline_reason ? h(M.Alert, { color: 'red', title: 'Declined' }, q.decline_reason) : null,
      h(M.Title, { order: 5 }, 'Items requested'),
      q.items.map((item) => h(QuoteItemCard, { key: item.id, item, files: q.files.filter((f) => f.item_id === item.id), editable, busy, act })),
      q.files.some((f) => !f.item_id)
        ? h(M.Group, { gap: 'xs' }, h(M.Text, { size: 'sm', c: 'dimmed' }, 'Other files:'),
            q.files.filter((f) => !f.item_id).map((f) => h(ExternalLink, { key: f.id, href: f.url }, f.name)))
        : null,
      h(M.Group, { justify: 'space-between' },
        h(M.Title, { order: 5 }, 'Quote'),
        q.lines.length ? h(PdfButton, { context, quote: q }) : null),
      q.pricing ? h(PricingNotice, { pricing: q.pricing }) : null,
      h(QuoteLines, { context, quote: q, editable: editable && !q.pricing, busy, act }),
      h(QuoteActions, { quote: q, busy, act }),
      h(StaffNotes, { quote: q, busy, act }),
    );
  });
}

// Another plugin (e.g. inventree-xero) prices this quote: say where, and link to it.
function PricingNotice({ pricing }) {
  const info = pricing.info || {};
  return h(
    M.Alert,
    { color: info.error ? 'orange' : 'blue', title: `Priced in ${pricing.name}` },
    h(M.Stack, { gap: 4 },
      h(M.Text, { size: 'sm' },
        `Edit the lines and send the quote in ${pricing.name}; the customer gets it from the shop. `
        + (pricing.partial_accept ? 'Customers can accept some lines.' : 'Customers accept the whole quote.')),
      info.number || info.status
        ? h(M.Group, { gap: 'xs' },
            info.number ? h(M.Text, { size: 'sm', fw: 600 }, info.number) : null,
            info.status ? h(M.Badge, { variant: 'light' }, info.status) : null,
            info.url ? h(ExternalLink, { href: info.url }, `Open in ${pricing.name}`) : null)
        : h(M.Text, { size: 'sm', c: 'dimmed' }, `Not in ${pricing.name} yet.`),
      info.error ? h(M.Text, { size: 'sm', c: 'orange' }, info.error) : null),
  );
}

function PdfButton({ context, quote }) {
  const [busy, setBusy] = useState(false);
  const draft = !['quoted', 'accepted', 'expired'].includes(quote.status);
  const open = () => {
    setBusy(true);
    context.api
      .get(`${BASE}/quotes/${quote.id}/pdf/`, { responseType: 'blob', timeout: 120000 })
      .then((r) => {
        const url = window.URL.createObjectURL(new window.Blob([r.data], { type: 'application/pdf' }));
        const opened = window.open(url, '_blank');
        if (!opened) {
          const a = document.createElement('a');
          a.href = url;
          a.download = `Quote-${quote.reference}.pdf`;
          a.click();
        }
        setTimeout(() => window.URL.revokeObjectURL(url), 60000);
      })
      .catch(async (e) => {
        let detail = errorText(e);
        const blob = e?.response?.data;
        if (blob && typeof blob.text === 'function') {
          try { detail = JSON.parse(await blob.text()).detail || detail; } catch (_) { /* not JSON */ }
        }
        notify('red', 'Quote PDF', detail);
      })
      .finally(() => setBusy(false));
  };
  return h(M.Button, { size: 'xs', variant: 'light', loading: busy, onClick: open },
    draft ? 'Preview PDF (draft)' : 'Download PDF');
}

function QuoteItemCard({ item, files, editable, busy, act }) {
  const [callNotes, setCallNotes] = useState('');
  const [reason, setReason] = useState('');
  const stateBadge = item.state === 'cannot'
    ? h(M.Badge, { color: 'red', variant: 'light' }, "Can't supply")
    : item.state === 'quoted' ? h(M.Badge, { color: 'teal', variant: 'light' }, 'Priced') : null;
  return h(
    M.Paper,
    { withBorder: true, p: 'sm', radius: 'md' },
    h(
      M.Stack,
      { gap: 6 },
      h(M.Group, { gap: 'xs' },
        h(M.Text, { fw: 600 }, `Item ${item.position}`),
        h(M.Badge, { variant: 'outline' }, item.kind_label),
        h(M.Badge, { variant: 'light' }, `Qty ${item.quantity}`),
        stateBadge),
      item.description ? h(M.Text, { size: 'sm', style: { whiteSpace: 'pre-wrap' } }, item.description) : null,
      h(Rows, { rows: [['OEM part number', item.oem_part_number], ['Machine', item.machine], ['Based on shop product', item.reference_sku]] }),
      files.length ? h(M.Group, { gap: 'xs' }, h(M.Text, { size: 'sm', c: 'dimmed' }, 'Files:'), files.map((f) => h(ExternalLink, { key: f.id, href: f.url }, f.name))) : null,
      item.sending_sample
        ? h(M.Group, { gap: 'xs' },
            h(M.Badge, { color: item.sample_received ? 'green' : 'orange', variant: 'light' }, item.sample_received ? 'Sample received' : 'Sample on its way'),
            !item.sample_received && editable
              ? h(M.Button, { size: 'xs', loading: busy, onClick: () => act({ action: 'sample_received', item_id: item.id }) }, 'Mark sample received')
              : null)
        : null,
      item.wants_call
        ? h(M.Stack, { gap: 4 },
            h(M.Group, { gap: 'xs' },
              h(M.Badge, { color: item.called ? 'green' : 'grape', variant: 'light' }, item.called ? 'Called' : 'Wants a call'),
              item.call_time ? h(M.Text, { size: 'xs', c: 'dimmed' }, `Best time: ${item.call_time}`) : null),
            item.call_notes ? h(M.Text, { size: 'sm' }, item.call_notes) : null,
            !item.called && editable
              ? h(M.Group, { gap: 'xs', align: 'flex-end' },
                  h(M.TextInput, { size: 'xs', placeholder: 'What was discussed', value: callNotes, onChange: (e) => setCallNotes(e.currentTarget.value), style: { flex: 1 } }),
                  h(M.Button, { size: 'xs', loading: busy, onClick: () => act({ action: 'called', item_id: item.id, notes: callNotes }) }, 'Record call'))
              : null)
        : null,
      item.state === 'cannot'
        ? h(M.Group, { gap: 'xs' },
            h(M.Text, { size: 'sm', c: 'red' }, item.cannot_reason),
            editable ? h(M.Button, { size: 'xs', variant: 'subtle', loading: busy, onClick: () => act({ action: 'reopen_item', item_id: item.id }) }, 'Reopen') : null)
        : editable
          ? h(M.Group, { gap: 'xs', align: 'flex-end' },
              h(M.TextInput, { size: 'xs', placeholder: "Reason (shown to the customer)", value: reason, onChange: (e) => setReason(e.currentTarget.value), style: { flex: 1 } }),
              h(M.Button, { size: 'xs', color: 'red', variant: 'light', loading: busy, onClick: () => act({ action: 'cannot', item_id: item.id, reason }) }, "Can't supply"))
          : null,
    ),
  );
}

function PartPicker({ context, value, label, onPick }) {
  const [options, setOptions] = useState(value ? [{ value: String(value), label: label || `Part ${value}` }] : []);
  const search = (text) => {
    if (!text || text.length < 2) return;
    context.api
      .get('/api/part/', { params: { search: text, salable: true, active: true, limit: 15 } })
      .then((r) => {
        const results = r.data.results || r.data || [];
        const found = results.map((p) => ({ value: String(p.pk), label: `${p.full_name || p.name}${p.description ? ' - ' + p.description : ''}` }));
        const current = options.filter((o) => o.value === String(value));
        setOptions([...current, ...found.filter((f) => f.value !== String(value))]);
      })
      .catch(() => {});
  };
  return h(M.Select, {
    size: 'xs', searchable: true, placeholder: 'Search salable parts', data: options, value: value ? String(value) : null,
    onSearchChange: search, filter: ({ options: o }) => o, nothingFoundMessage: 'Type to search',
    onChange: (v) => { const opt = options.find((o) => o.value === v); onPick(v ? parseInt(v, 10) : null, opt ? opt.label : ''); },
    style: { minWidth: 220 },
  });
}

function QuoteLines({ context, quote, editable, busy, act }) {
  const blank = (charge) => ({ item_id: charge ? null : quote.items[0]?.id || null, part_id: null, part_name: '', description: '', quantity: 1, unit_price: '', lead_time: '', charge: !!charge });
  const [rows, setRows] = useState(quote.lines.map((l) => ({ ...l })));
  useEffect(() => setRows(quote.lines.map((l) => ({ ...l }))), [quote]);
  const update = (i, patch) => setRows(rows.map((r, n) => (n === i ? { ...r, ...patch } : r)));
  const total = rows.reduce((sum, r) => sum + (parseFloat(r.unit_price) || 0) * (parseInt(r.quantity, 10) || 0), 0);
  const itemOptions = quote.items.map((it) => ({ value: String(it.id), label: `Item ${it.position}` }));

  if (!editable) {
    if (!quote.lines.length) return h(M.Text, { size: 'sm', c: 'dimmed' }, 'No lines.');
    return h(
      M.Table,
      { fz: 'sm', verticalSpacing: 4 },
      h(M.Table.Thead, null, h(M.Table.Tr, null, ['Item', 'Part', 'Description', 'Qty', 'Unit', 'Total', 'Lead time', 'Accepted'].map((c) => h(M.Table.Th, { key: c }, c)))),
      h(M.Table.Tbody, null, quote.lines.map((l) =>
        h(M.Table.Tr, { key: l.id },
          h(M.Table.Td, null, itemOptions.find((o) => o.value === String(l.item_id))?.label || ''),
          h(M.Table.Td, null, l.part_id
            ? h(InternalLink, { context, to: `/part/${l.part_id}/` }, l.part_name)
            : h(M.Badge, { variant: 'outline', color: 'gray' }, 'Charge')),
          h(M.Table.Td, null, l.description),
          h(M.Table.Td, null, String(l.quantity)),
          h(M.Table.Td, null, l.unit_price),
          h(M.Table.Td, null, l.total),
          h(M.Table.Td, null, l.lead_time),
          h(M.Table.Td, null, l.accepted ? 'Yes' : '')))),
      h(M.Table.Tfoot, null, h(M.Table.Tr, null, h(M.Table.Td, { colSpan: 5 }, h(M.Text, { size: 'sm', fw: 600, ta: 'right' }, 'Total (ex tax)')), h(M.Table.Td, null, h(M.Text, { size: 'sm', fw: 600 }, `${quote.total} ${quote.currency}`)), h(M.Table.Td, { colSpan: 2 }))),
    );
  }

  return h(
    M.Stack,
    { gap: 'xs' },
    rows.map((r, i) =>
      h(M.Group, { key: i, gap: 'xs', align: 'flex-end', wrap: 'wrap' },
        h(M.Select, { size: 'xs', data: itemOptions, value: r.item_id ? String(r.item_id) : null, onChange: (v) => update(i, { item_id: v ? parseInt(v, 10) : null }), w: 100, 'aria-label': 'Item', clearable: true }),
        r.charge
          ? h(M.Badge, { variant: 'outline', color: 'gray', w: 220 }, 'Charge (no part)')
          : h(PartPicker, { context, value: r.part_id, label: r.part_name, onPick: (pk, name) => update(i, { part_id: pk, part_name: name }) }),
        h(M.TextInput, { size: 'xs', placeholder: r.charge ? 'What it is - labour, freight... (required)' : 'Description for the customer (optional)', value: r.description, onChange: (e) => update(i, { description: e.currentTarget.value }), style: { flex: 1, minWidth: 180 } }),
        h(M.NumberInput, { size: 'xs', min: 1, value: r.quantity, onChange: (v) => update(i, { quantity: v }), w: 70, 'aria-label': 'Quantity' }),
        h(M.TextInput, { size: 'xs', placeholder: 'Unit price', value: r.unit_price, onChange: (e) => update(i, { unit_price: e.currentTarget.value }), w: 100 }),
        h(M.TextInput, { size: 'xs', placeholder: 'Lead time (default if blank)', value: r.lead_time, onChange: (e) => update(i, { lead_time: e.currentTarget.value }), w: 170 }),
        h(M.Button, { size: 'xs', variant: 'subtle', color: 'red', onClick: () => setRows(rows.filter((_, n) => n !== i)) }, 'Remove'),
      ),
    ),
    h(M.Group, { justify: 'space-between' },
      h(M.Group, { gap: 'xs' },
        h(M.Button, { size: 'xs', variant: 'light', onClick: () => setRows([...rows, blank(false)]) }, 'Add line'),
        h(M.Button, { size: 'xs', variant: 'light', onClick: () => setRows([...rows, blank(true)]) }, 'Add charge (labour, freight)'),
        h(M.Button, { size: 'xs', loading: busy, onClick: () => act({ action: 'set_lines', lines: rows.map((r) => (r.charge ? { ...r, part_id: null } : r)) }) }, 'Save lines')),
      h(M.Text, { size: 'sm', fw: 600 }, `Total (ex tax): ${total.toFixed(2)} ${quote.currency}`)),
  );
}

function QuoteActions({ quote, busy, act }) {
  const [days, setDays] = useState(30);
  const [reason, setReason] = useState('');
  const editable = OPEN_STATUSES.includes(quote.status);
  const pricedHere = !quote.pricing; // with a pricing plugin, sending and reopening happen there
  if (!editable && quote.status !== 'quoted') return null;
  return h(
    M.Paper,
    { withBorder: true, p: 'sm', radius: 'md' },
    h(
      M.Group,
      { gap: 'sm', align: 'flex-end', wrap: 'wrap' },
      editable && quote.status !== 'reviewing'
        ? h(M.Button, { size: 'xs', variant: 'light', loading: busy, onClick: () => act({ action: 'reviewing' }) }, 'Mark reviewing')
        : null,
      editable && pricedHere ? h(M.NumberInput, { size: 'xs', label: 'Valid for (days)', min: 1, value: days, onChange: setDays, w: 120 }) : null,
      editable && pricedHere ? h(M.Button, { size: 'xs', color: 'teal', loading: busy, onClick: () => act({ action: 'send', valid_days: days }) }, 'Send quote') : null,
      quote.status === 'quoted' && pricedHere ? h(M.Button, { size: 'xs', variant: 'light', loading: busy, onClick: () => act({ action: 'reopen' }) }, 'Reopen to change') : null,
      h(M.TextInput, { size: 'xs', label: 'Decline reason', value: reason, onChange: (e) => setReason(e.currentTarget.value), style: { flex: 1, minWidth: 200 } }),
      h(M.Button, { size: 'xs', color: 'red', variant: 'light', loading: busy, onClick: () => act({ action: 'decline', reason }, 'orange') }, 'Decline'),
    ),
  );
}

function StaffNotes({ quote, busy, act }) {
  const [text, setText] = useState(quote.staff_notes || '');
  return h(M.Stack, { gap: 4 },
    h(M.Textarea, { label: 'Internal notes (never shown to the customer)', autosize: true, minRows: 2, value: text, onChange: (e) => setText(e.currentTarget.value) }),
    h(M.Group, null, h(M.Button, { size: 'xs', variant: 'subtle', loading: busy, onClick: () => act({ action: 'notes', staff_notes: text }) }, 'Save notes')));
}

// ---------------------------------------------------------------- entry points

export function renderShopDashboard(context) {
  return h(Dashboard, { context });
}

export function renderQuoteDashboard(context) {
  return h(QuoteDashboard, { context });
}

export function renderShopPartPanel(context) {
  return h(PartPanel, { context, key: `part-${context.id}` });
}

export function renderShopOrderPanel(context) {
  return h(OrderPanel, { context, key: `so-${context.id}` });
}
