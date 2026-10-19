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
g.requestAnimationFrame = (cb) => setTimeout(cb, 0);
g.cancelAnimationFrame = (id) => clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.matchMedia = dom.window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
g.ResizeObserver = dom.window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

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

const posted = [];
const navigated = [];
function makeContext(id) {
  return {
    id,
    api: {
      get: async (url) => { if (!(url in fixtures)) throw new Error('unexpected GET ' + url); return { data: fixtures[url] }; },
      post: async (url) => { posted.push(url); return { data: { detail: 'Queued.', summary: {} } }; },
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

const reactErrors = errors.filter((e) => !e.includes('Not implemented: window.computedStyle') && !e.includes('Could not parse CSS'));
console.error = origError;
check('no React errors or warnings', reactErrors.length === 0);
if (reactErrors.length) console.log(reactErrors.slice(0, 5).join('\n'));

console.log(failures ? `\n${failures} FAILED` : '\nALL PANEL CHECKS PASSED');
process.exit(failures ? 1 : 0);
