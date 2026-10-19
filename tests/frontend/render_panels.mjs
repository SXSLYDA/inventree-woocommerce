/**
 * Renders the three panels in jsdom with React 19 + Mantine 9 (what InvenTree 1.5/1.6 ship)
 * and a fake InvenTree plugin context. Run from a folder where those are installed:
 *   npm i react@19 react-dom@19 @mantine/core@9 @mantine/hooks@9 @mantine/notifications@9 jsdom
 *   node render_panels.mjs /path/to/shop_panels.js
 */
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const require = createRequire(process.cwd() + '/');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'https://inventree.example.com/' });
const g = globalThis;
g.window = dom.window;
g.document = dom.window.document;
Object.defineProperty(g, 'navigator', { value: dom.window.navigator, configurable: true });
g.HTMLElement = dom.window.HTMLElement;
g.Element = dom.window.Element;
g.Node = dom.window.Node;
g.getComputedStyle = dom.window.getComputedStyle;
g.requestAnimationFrame = dom.window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
g.cancelAnimationFrame = dom.window.cancelAnimationFrame = (id) => clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.matchMedia = dom.window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
g.ResizeObserver = dom.window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
for (const name of ['MutationObserver', 'Event', 'MouseEvent', 'KeyboardEvent', 'HTMLInputElement', 'DocumentFragment', 'DOMRect']) {
  if (dom.window[name] && !g[name]) g[name] = dom.window[name];
}
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
if (!dom.window.document.fonts) {
  dom.window.document.fonts = { addEventListener() {}, removeEventListener() {}, ready: Promise.resolve(), status: 'loaded' };
}
dom.window.Element.prototype.getAnimations = dom.window.Element.prototype.getAnimations || (() => []);

const React = require('react');
const ReactDOMClient = require('react-dom/client');
const MantineCore = require('@mantine/core');
const MantineNotifications = require('@mantine/notifications');
Object.assign(dom.window, { React, MantineCore, MantineNotifications });

const panels = await import(pathToFileURL(process.argv[2]).href);
const { act } = React;

const fixtures = {
  '/plugin/woocommerce/part/12/': {
    part_id: 12, in_shop: false, mode: 'build', reason: 'Waiting for release: BOM not validated.', held: 'BOM not validated',
    flag: 'Build', in_category: false, price: '850.00', currency: 'AUD', draft_no_price: false, available: null,
    revision: 'B', documents: [{ label: 'Wiring drawing', url: 'https://inventree.example.com/plugin/woocommerce/file/4/abc/drawing.pdf' }],
    product: { id: 502, url: 'https://shop.example.com/product/omg263', admin_url: 'https://shop.example.com/wp-admin/post.php?post=502&action=edit', published: false, stock_sent: 0, last_synced: '2026-10-19T08:00:00Z', error: '' },
    can_manage: true,
  },
  '/plugin/woocommerce/salesorder/7/': {
    found: true, pk: 3, woo_order_id: 9101, number: '9101', woo_status: 'processing', admin_url: 'https://shop.example.com/wp-admin/post.php?post=9101&action=edit',
    on_account: true, held: true, po_number: 'PO-555', payment_method: 'Charge to my account', transaction_id: '', total: '900.00', total_tax: '0', currency: 'AUD',
    customer_note: '', warnings: ['Over credit limit: order 900.00 AUD, 700.00 AUD available'], builds: [{ pk: 5, reference: 'BO-0005' }], received_at: '2026-10-19T08:00:00Z',
  },
  '/plugin/woocommerce/status/': {
    configured: true, problems: ['Webhook secret not set - paid orders can\'t be received.'],
    products: { linked: 3, published: 2, with_errors: 1 }, accounts: { logins_linked: 2, logins_approved: 1 },
    last_sync: '2026-10-19T08:00:00Z', last_full_sync: null, last_summary: { created: 1, updated: 2, unchanged: 5, unpublished: 0, errors: 1 },
    awaiting_release: [{ part_id: 12, name: 'OMG263-3620', reason: 'BOM not validated' }],
    product_errors: [{ part_id: 4, woo_product_id: 501, last_error: 'product_invalid_sku: SKU already used', name: 'DT06-2S' }],
    orders: [
      { pk: 1, woo_order_number: '9001', woo_status: 'processing', state: 'created', sales_order_id: 7, sales_order_reference: 'SO-0007', on_account: false, held: false, build_order_ids: [5], warnings: [], error: '', received_at: null },
      { pk: 2, woo_order_number: '9002', woo_status: 'processing', state: 'failed', sales_order_id: null, sales_order_reference: '', on_account: true, held: false, build_order_ids: [], warnings: [], error: 'Shared customer not set', received_at: null },
    ],
    woo_url: 'https://shop.example.com',
  },
};

const quoteDetail = {
  id: 1, reference: 'QR-261019-TEST', status: 'waiting_sample', status_label: 'Waiting for sample', name: 'Bob',
  company_name: 'Acme', email: 'buyer@acme.example', phone: '0400 000 000', created_at: '2026-10-19T08:00:00Z',
  required_by: '2026-12-01', delivery_postcode: '6430', notes: 'Two machines', currency: 'AUD', valid_until: null,
  decline_reason: '', staff_notes: '', sales_order_id: null, total: '0.00',
  items: [
    { id: 11, position: 1, kind: 'custom', kind_label: 'Custom harness', description: 'Engine loom like the sample', oem_part_number: '',
      machine: 'CAT R1300G', quantity: 2, reference_sku: '', sending_sample: true, sample_received: false, wants_call: false,
      call_time: '', called: false, state: 'open', cannot_reason: '', call_notes: '' },
    { id: 12, position: 2, kind: 'copy', kind_label: 'Copy of an existing harness', description: 'Dash harness', oem_part_number: '123-4567',
      machine: '', quantity: 1, reference_sku: '', sending_sample: false, sample_received: false, wants_call: true,
      call_time: 'Mornings', called: false, state: 'open', cannot_reason: '', call_notes: '' },
  ],
  files: [{ id: 5, item_id: 12, name: 'layout.pdf', size: 24, url: '/plugin/woocommerce/quotes/1/files/5/' }],
  lines: [],
};
fixtures['/plugin/woocommerce/quotes/?status=open'] = {
  quotes: [{ id: 1, reference: 'QR-261019-TEST', status: 'waiting_sample', status_label: 'Waiting for sample', name: 'Bob',
             company_name: 'Acme', created_at: '2026-10-19T08:00:00Z', required_by: '2026-12-01', valid_until: null,
             items: 2, samples_due: 1, calls_due: 1, sales_order_id: null }],
  counts: {},
};
fixtures['/plugin/woocommerce/quotes/1/'] = quoteDetail;
fixtures['/api/part/'] = { count: 1, results: [{ pk: 42, name: 'OMG-CUSTOM-1', full_name: 'OMG-CUSTOM-1', description: 'Custom loom' }] };

const posted = [];
const postedBodies = [];
const partSearches = [];
const navigated = [];
function makeContext(id) {
  return {
    id,
    api: {
      get: async (url, config) => {
        if (!(url in fixtures)) throw new Error('unexpected GET ' + url);
        if (url === '/api/part/') partSearches.push(config?.params);
        return { data: fixtures[url] };
      },
      post: async (url, body) => {
        posted.push(url);
        postedBodies.push(body);
        if (url === '/plugin/woocommerce/quotes/1/') {
          const q = JSON.parse(JSON.stringify(quoteDetail));
          if (body.action === 'sample_received') { q.items[0].sample_received = true; q.status = 'call'; q.status_label = 'Call to discuss'; }
          if (body.action === 'set_lines') { q.lines = body.lines.map((l, n) => ({ ...l, id: n + 1, part_name: 'OMG-CUSTOM-1', total: '0', accepted: false })); }
          return { data: { detail: 'Done.', quote: q } };
        }
        return { data: { detail: 'Queued.', summary: {} } };
      },
    },
    navigate: (to) => navigated.push(to),
    user: { isStaff: () => true },
  };
}

let failures = 0;
function check(label, cond) { console.log((cond ? 'ok   ' : 'FAIL ') + label); if (!cond) failures++; }

async function render(name, el) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOMClient.createRoot(container);
  await act(async () => {
    root.render(React.createElement(MantineCore.MantineProvider, null, el));
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  return { container, root };
}

const errors = [];
const origError = console.error;
console.error = (...a) => { errors.push(a.map(String).join(' ')); };

// part panel
const part = await render('part', panels.renderShopPartPanel(makeContext(12)));
const pt = part.container.textContent;
check('part: shows waiting-for-release badge and reason', pt.includes('Waiting for release') && pt.includes('BOM not validated'));
check('part: shows build-to-order mode and revision', pt.includes('Built to order') && pt.includes('Revision') && pt.includes('B'));
check('part: lists the document with its link', !!part.container.querySelector('a[href$="drawing.pdf"]') && pt.includes('Wiring drawing'));
check('part: links to the product page and WooCommerce admin', !!part.container.querySelector('a[href="https://shop.example.com/product/omg263"]') && pt.includes('Edit product #502'));
const sendBtn = [...part.container.querySelectorAll('button')].find((b) => b.textContent.includes('Send to shop now'));
check('part: staff see "Send to shop now"', !!sendBtn);
await act(async () => { sendBtn.click(); await new Promise((r) => setTimeout(r, 20)); });
check('part: button POSTs to the part endpoint', posted.includes('/plugin/woocommerce/part/12/'));

// sales order panel
const so = await render('so', panels.renderShopOrderPanel(makeContext(7)));
const st = so.container.textContent;
check('order: account + on hold badges', st.includes('Charged to account') && st.includes('Put on hold'));
check('order: warning shown', st.includes('Over credit limit'));
check('order: PO and web order link', st.includes('PO-555') && !!so.container.querySelector('a[href*="post=9101"]'));
const boLink = [...so.container.querySelectorAll('a')].find((a) => a.textContent === 'BO-0005');
await act(async () => { boLink.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })); });
check('order: Build Order link navigates inside InvenTree', navigated.includes('/manufacturing/build-order/5'));

// dashboard
const dash = await render('dash', panels.renderShopDashboard(makeContext(undefined)));
const dt = dash.container.textContent;
check('dashboard: problem shown', dt.includes('Webhook secret not set'));
check('dashboard: stats', dt.includes('In the shop') && dt.includes('Awaiting release'));
check('dashboard: awaiting release + sync problem listed', dt.includes('OMG263-3620') && dt.includes('SKU already used'));
check('dashboard: failed order with Retry', dt.includes('Shared customer not set') && [...dash.container.querySelectorAll('button')].some((b) => b.textContent === 'Retry'));
check('dashboard: recent orders link to the Sales Order', dt.includes('SO-0007'));
const syncBtn = [...dash.container.querySelectorAll('button')].find((b) => b.textContent === 'Sync whole shop');
await act(async () => { syncBtn.click(); await new Promise((r) => setTimeout(r, 20)); });
check('dashboard: Sync whole shop POSTs', posted.includes('/plugin/woocommerce/sync/'));

// quote requests widget
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
const quotes = await render('quotes', panels.renderQuoteDashboard(makeContext(undefined)));
const qt = quotes.container.textContent;
check('quotes: list shows the request with samples and calls due', qt.includes('QR-261019-TEST') && qt.includes('1 sample') && qt.includes('1 call'));
const row = [...quotes.container.querySelectorAll('tr')].find((tr) => tr.textContent.includes('QR-261019-TEST'));
await act(async () => { row.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await new Promise((r) => setTimeout(r, 50)); });
const page = () => document.body.textContent;
check('quotes: clicking opens the request', page().includes('Engine loom like the sample') && page().includes('Items requested'));
check('quotes: item files and call details shown', page().includes('layout.pdf') && page().includes('Best time: Mornings'));
const sampleBtn = [...document.body.querySelectorAll('button')].find((b) => b.textContent === 'Mark sample received');
await act(async () => { sampleBtn.click(); await new Promise((r) => setTimeout(r, 50)); });
check('quotes: mark sample received posts the action', postedBodies.some((b) => b && b.action === 'sample_received' && b.item_id === 11));
check('quotes: view updates from the response', page().includes('Sample received') && page().includes('Call to discuss'));
const addBtn = [...document.body.querySelectorAll('button')].find((b) => b.textContent === 'Add line');
await act(async () => { addBtn.click(); await new Promise((r) => setTimeout(r, 20)); });
const priceInput = [...document.body.querySelectorAll('input')].find((i) => i.placeholder === 'Unit price');
check('quotes: adding a line shows the line editor', !!priceInput);
const searchInput = [...document.body.querySelectorAll('input')].find((i) => i.placeholder === 'Search salable parts');
await act(async () => {
  searchInput.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  searchInput.focus();
  await new Promise((r) => setTimeout(r, 20));
});
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(searchInput, 'OMG');
  searchInput.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));
});
const option = [...document.body.querySelectorAll('[role="option"]')].find((o) => o.textContent.includes('OMG-CUSTOM-1'));
check('quotes: part search asks InvenTree for salable, active parts', partSearches.some((p) => p && p.search === 'OMG' && p.salable === true && p.active === true));
check('quotes: part search offers salable parts from InvenTree', !!option);
await act(async () => { option.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await new Promise((r) => setTimeout(r, 20)); });
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(priceInput, '1450');
  priceInput.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
});
const saveBtn = [...document.body.querySelectorAll('button')].find((b) => b.textContent === 'Save lines');
await act(async () => { saveBtn.click(); await new Promise((r) => setTimeout(r, 50)); });
const saved = postedBodies.find((b) => b && b.action === 'set_lines');
check('quotes: save lines sends part, item and price', !!saved && saved.lines[0].part_id === 42 && saved.lines[0].unit_price === '1450' && saved.lines[0].item_id === 11);
check('quotes: send and decline actions available', [...document.body.querySelectorAll('button')].some((b) => b.textContent === 'Send quote') && page().includes('Decline reason'));

const reactErrors = errors.filter((e) => !e.includes('Not implemented: window.computedStyle') && !e.includes('Could not parse CSS'));
console.error = origError;
check('no React errors or warnings', reactErrors.length === 0);
if (reactErrors.length) console.log(reactErrors.slice(0, 5).join('\n'));

console.log(failures ? `\n${failures} FAILED` : '\nALL PANEL CHECKS PASSED');
process.exit(failures ? 1 : 0);
