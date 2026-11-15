'use strict';
/* Food section: cart, menu, food checkout, payment and order confirmation.
   Loaded AFTER tickets.js and BEFORE app.js (like tickets.js it only declares things; it uses app.js / tickets.js helpers
   (api, show, esc, inr, toast, need, state, payPill, fdt, wireDownloads...) at call time).
   Prices shown here are for display; the server's /food/quote and /food/orders are the only authority on money. */
const FOOD_MAX_QTY = 20;                       // same limit the API enforces per line

// ---------- cart (pure logic, no DOM: unit-testable in Node) ----------
function makeCart(storage, onChange){
  const KEY = 'bmm_cart';                      // same key and {itemId: qty} shape the app already used
  let mem = {};
  const clean = obj => { const o = {}; if (obj && typeof obj === 'object') for (const [k, v] of Object.entries(obj)) {
    const q = Math.floor(Number(v)); if (/^\d+$/.test(k) && Number.isFinite(q) && q > 0) o[k] = Math.min(q, FOOD_MAX_QTY); } return o; };
  const read = () => { try { return storage ? clean(JSON.parse(storage.getItem(KEY) || '{}')) : clean(mem); } catch { return clean(mem); } };
  const write = c => { mem = c; try { storage && storage.setItem(KEY, JSON.stringify(c)); } catch { /* private mode / quota: keep in memory */ } onChange && onChange(c); };
  return {
    get: read,
    set(c){ write(clean(c)); },
    count(){ return Object.values(read()).reduce((a, b) => a + b, 0); },
    qty(id){ return read()[id] || 0; },
    setQty(id, q){ const c = read(); q = Math.min(Math.floor(Number(q)) || 0, FOOD_MAX_QTY); if (q > 0) c[id] = q; else delete c[id]; write(c); },
    add(id, n = 1){ this.setQty(id, this.qty(id) + n); },
    remove(id){ this.setQty(id, 0); },
    clear(){ write({}); },
  };
}
const cart = makeCart(typeof localStorage !== 'undefined' ? localStorage : null, () => { if (typeof badge === 'function') badge(); });

// ---------- presentation helpers (pure) ----------
const FOOD_EMOJI = [[/brownie|chocolate/i,'🍫'],[/pastry|cake/i,'🍰'],[/waffle/i,'🧇'],[/donut|doughnut/i,'🍩'],[/ice ?cream/i,'🍨'],
  [/pani ?puri|dahipuri|puri/i,'🥟'],[/samosa/i,'🥟'],[/vada ?pav|burger/i,'🍔'],[/pav ?bhaji/i,'🍛'],[/water/i,'💧'],[/milkshake|shake/i,'🥤'],
  [/soft drink|cola|soda/i,'🥤'],[/tea/i,'🍵'],[/coffee/i,'☕'],[/fried rice|rice/i,'🍚'],[/pizza/i,'🍕'],[/popcorn/i,'🍿'],
  [/sandwich/i,'🥪'],[/parotta|paratha/i,'🫓']];
const FOOD_CAT_EMOJI = [[/dessert/i,'🍰'],[/chat/i,'🥟'],[/beverage|drink/i,'🥤'],[/main/i,'🍽️']];
function foodEmoji(item){
  const hit = FOOD_EMOJI.find(([rx]) => rx.test(item.name || '')) || FOOD_CAT_EMOJI.find(([rx]) => rx.test(item.category || ''));
  return hit ? hit[1] : '🍴';
}
function foodMatches(item, q, cat){
  if (cat && cat !== 'all' && String(item.category_id) !== String(cat)) return false;
  const t = (q || '').trim().toLowerCase();
  return !t || [item.name, item.description, item.category].some(s => (s || '').toLowerCase().includes(t));
}
/* Splits the cart into lines that exist on the menu and ids that do not (unavailable). `gone` = ids the server flagged. */
function cartLinesFor(c, menu, goneIds = []){
  const byId = new Map(menu.map(i => [String(i.id), i])), gone = new Set(goneIds.map(String)), lines = [], missing = [];
  for (const [id, qty] of Object.entries(c)) { const it = byId.get(id); (it && !gone.has(id) ? lines : missing).push(it && !gone.has(id) ? {...it, qty} : {id, qty}); }
  return {lines, missing, estimate: lines.reduce((a, l) => a + l.price * l.qty, 0)};
}
function foodBillRows(b, rates = {}){            // b: {subtotal, fee, tax, total}
  return `<tr><td>Subtotal</td><td align="right">${inr(b.subtotal)}</td></tr>`
    + (b.fee ? `<tr><td>Service fee${rates.food_fee_percent ? ` (${rates.food_fee_percent}%)` : ''}</td><td align="right">${inr(b.fee)}</td></tr>` : '')
    + `<tr><td>Tax${rates.food_tax_percent ? ` (${rates.food_tax_percent}%)` : ''}</td><td align="right">${inr(b.tax)}</td></tr>`
    + `<tr class="grand"><td>Total</td><td align="right">${inr(b.total)}</td></tr>`;
}
const foodItemRows = lines => lines.map(l => `<tr><td>${esc(l.name)} <span class="muted">× ${l.quantity}</span><div class="muted small">${inr(l.unit_price)} each</div></td><td align="right">${inr(l.line_total)}</td></tr>`).join('');
/* Items of the live food orders of a booking, for the ticket payment summary */
function bookingFoodRows(orders){
  const live = (orders || []).filter(o => o.status === 'placed');
  return live.map(o => o.items.map(i => `<tr class="sub"><td>🍴 ${esc(i.name)} × ${i.quantity}</td><td align="right">${inr(i.line_total)}</td></tr>`).join('')
    + (o.fee ? `<tr class="sub"><td>Food service fee</td><td align="right">${inr(o.fee)}</td></tr>` : '')
    + `<tr class="sub"><td>Food tax</td><td align="right">${inr(o.tax)}</td></tr>`).join('');
}
const foodArt = i => i.image_url ? `<img src="${esc(i.image_url)}" alt="${esc(i.name)}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'emoji',textContent:'${foodEmoji(i)}'}))">` : `<span class="emoji">${foodEmoji(i)}</span>`;
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random())).replace(/-/g, '');

// ---------- routing: #/food, #/food/orders, #/food/order/:id, #/food/pay/:id ----------
async function foodPage(seg = []){
  const sub = seg[1];
  if (!sub) return foodMenuPage();
  if (sub === 'orders') return foodOrdersPage();
  if (sub === 'order') return foodOrderPage(seg[2]);
  if (sub === 'pay') return foodPayPage(seg[2]);
  show('<div class="empty">Page not found. <a href="#/food">Back to the menu</a></div>');
}

// ---------- menu + cart ----------
async function foodMenuPage(){
  loading();
  const [cats, menu] = await Promise.all([api('/food/categories'), api('/food/items')]);
  const bookings = state.user ? (await api('/bookings')).filter(b => b.status === 'confirmed' || b.status === 'pending_payment') : [];
  const S = {q: '', cat: 'all', quote: null, pending: false, seq: 0, timer: null, idem: null, busy: false, booking: ''};
  show(`<div class="food-head"><div><h1>Food menu</h1><p class="muted">Order snacks and drinks for match day. Pick up at the stadium food counter.</p></div>
      ${state.user ? '<a class="btn ghost" href="#/food/orders">My food orders</a>' : ''}</div>
    <div class="food-tools"><input id="fsearch" type="search" placeholder="Search food, e.g. samosa, coffee…" aria-label="Search the menu" autocomplete="off">
      <div class="chips" id="chips"></div></div>
    <div class="food-layout"><section id="menu" aria-live="polite"></section><aside class="card cartpanel" id="cartPanel" aria-label="Your cart"></aside></div>
    <button class="fab" id="fab" hidden>🛒 View cart <b id="fabN"></b></button>`);

  const card = i => { const n = cart.qty(i.id); return `<article class="fcard" data-id="${i.id}"><div class="fimg">${foodArt(i)}</div>
      <div class="fbody"><span class="ftag">${esc(i.category)}</span><h3>${esc(i.name)}</h3><p class="fdesc">${esc(i.description || 'Freshly served at the stadium food counter.')}</p>
      <div class="frow"><span class="price">${inr(i.price)}</span><div class="fctl" data-ctl="${i.id}">${ctl(i.id, n)}</div></div></div></article>`; };
  const ctl = (id, n) => n ? `<div class="qty pop"><button data-dec="${id}" aria-label="Remove one">−</button><b>${n}</b><button data-inc="${id}" aria-label="Add one" ${n >= FOOD_MAX_QTY ? 'disabled' : ''}>+</button></div>`
    : `<button class="btn sm" data-add="${id}">Add to cart</button>`;
  const renderChips = () => { $('#chips').innerHTML = [{id: 'all', name: 'All'}, ...cats].map(c => `<button class="chip ${String(c.id) === String(S.cat) ? 'on' : ''}" data-cat="${c.id}">${esc(c.name)}</button>`).join(''); };
  const renderMenu = () => {
    const shown = menu.filter(i => foodMatches(i, S.q, S.cat));
    $('#menu').innerHTML = !shown.length ? `<div class="empty">No food matches “${esc(S.q)}”. <button class="btn ghost sm" data-reset>Clear filters</button></div>`
      : cats.map(c => ({c, list: shown.filter(i => i.category_id === c.id)})).filter(g => g.list.length)
        .map(g => `<h2 class="fcat">${esc(g.c.name)} <span class="muted small">${g.list.length}</span></h2><div class="fgrid">${g.list.map(card).join('')}</div>`).join('');
  };
  const refreshControls = id => document.querySelectorAll(`[data-ctl="${id}"]`).forEach(el => { el.innerHTML = ctl(id, cart.qty(id)); });

  const renderCart = () => {
    const c = cart.get(), q = S.quote, {lines, missing, estimate} = cartLinesFor(c, menu, q ? q.unavailable.map(u => u.item_id) : []);
    const n = cart.count(); $('#fab').hidden = !n; $('#fabN').textContent = n ? `(${n})` : '';
    const nameOf = id => (q && (q.unavailable.find(u => String(u.item_id) === String(id)) || {}).name) || `Item #${id}`;
    let html = `<h2>Your cart ${n ? `<span class="badge">${n}</span>` : ''}</h2>`;
    if (!lines.length && !missing.length) { $('#cartPanel').innerHTML = html + '<div class="empty">Your cart is empty.<br><span class="small">Add something tasty from the menu.</span></div>'; return; }
    html += `<ul class="clines">${lines.map(l => `<li><div class="cl-main"><b>${esc(l.name)}</b><span class="muted small">${inr(l.price)} each</span></div>
      <div class="qty"><button data-dec="${l.id}" aria-label="Decrease ${esc(l.name)}">−</button><b>${l.qty}</b><button data-inc="${l.id}" aria-label="Increase ${esc(l.name)}" ${l.qty >= FOOD_MAX_QTY ? 'disabled' : ''}>+</button></div>
      <span class="cl-total">${inr(l.price * l.qty)}</span><button class="btn ghost sm" data-rm="${l.id}" aria-label="Remove ${esc(l.name)}">✕</button></li>`).join('')}
      ${missing.map(m => `<li class="gone"><div class="cl-main"><b>${esc(nameOf(m.id))}</b><span class="small">No longer available</span></div><span class="muted">× ${m.qty}</span><span></span><button class="btn ghost sm" data-rm="${m.id}" aria-label="Remove unavailable item">✕</button></li>`).join('')}</ul>`;
    if (missing.length) html += '<p class="notice warn">Some items are no longer available and are not charged. Remove them to continue.</p>';
    html += `<table class="totals">${S.err ? `<tr><td colspan="2" class="error small">${esc(S.err)}</td></tr>` : q && !S.pending ? foodBillRows(q, q.rates)
      : `<tr><td>Subtotal</td><td align="right">${inr(estimate)}</td></tr><tr class="grand"><td>Total</td><td align="right" class="muted">calculating…</td></tr>`}</table>`;
    const sel = bookings.find(b => String(b.id) === String(S.booking));
    html += state.user ? `<label for="fb">Order for</label><select id="fb"><option value="">Standalone pickup order</option>${bookings.map(b => `<option value="${b.id}" ${String(b.id) === String(S.booking) ? 'selected' : ''}>${esc(b.reference)} · ${esc(b.match)}${b.status === 'pending_payment' ? ' (unpaid)' : ''}</option>`).join('')}</select>
      <button class="btn block" id="checkout" ${(!lines.length || missing.length || S.pending || S.busy || S.err) ? 'disabled' : ''}>${sel && sel.status === 'pending_payment' ? 'Add to booking &amp; pay with tickets' : 'Place order &amp; pay'}</button>
      ${missing.length ? '<button class="btn ghost block" id="rmGone">Remove unavailable items</button>' : ''}
      <p class="notice">Demo mode: payment is simulated, no real money is charged. Prices are calculated by the server.</p>`
      : '<p class="muted">Log in to place an order.</p><a class="btn block" href="#/login" id="loginGo">Log in</a>';
    $('#cartPanel').innerHTML = html;
  };
  const scheduleQuote = () => {
    clearTimeout(S.timer); S.idem = null;
    const items = Object.entries(cart.get()).map(([id, quantity]) => ({item_id: +id, quantity}));
    if (!items.length) { S.quote = null; S.pending = false; S.err = null; S.seq++; return renderCart(); }
    S.pending = true; renderCart();
    S.timer = setTimeout(async () => {
      const my = ++S.seq;
      try { const r = await api('/food/quote', {method: 'POST', body: {items}}); if (my !== S.seq) return; S.quote = r; S.err = null; }
      catch (e) { if (my !== S.seq) return; S.quote = null; S.err = e.message; }
      S.pending = false; renderCart();
    }, 200);
  };
  const changed = id => { if (id != null) refreshControls(id); scheduleQuote(); };
  window.BMM_foodCartChanged = () => { if ($('#cartPanel')) { renderMenu(); scheduleQuote(); } };   // another tab changed the cart

  async function checkout(){
    const items = Object.entries(cart.get()).map(([id, quantity]) => ({item_id: +id, quantity})); if (!items.length || S.busy) return;
    S.busy = true; renderCart(); S.idem = S.idem || newKey();
    try {
      const o = await api('/food/orders', {method: 'POST', headers: {'Idempotency-Key': S.idem}, body: {items, booking_id: S.booking ? +S.booking : null}});
      cart.clear();
      if (o.pay_with_booking) { toast(`Food added to booking ${o.booking_reference}. Pay with your tickets.`); location.hash = '#/pay/' + o.booking_id; }
      else { toast(`Order ${o.order_number} placed. Complete the payment to confirm it.`); location.hash = '#/food/pay/' + o.id; }
    } catch (e) { toast(e.message, true); S.busy = false; renderCart(); }
  }

  $('#view').onclick = async e => {
    handleGo(e);
    const t = e.target.closest('button, a'); if (!t) return;
    const d = t.dataset;
    if (d.add) { cart.add(+d.add); return changed(d.add); }
    if (d.inc) { cart.add(+d.inc); return changed(d.inc); }
    if (d.dec) { cart.add(+d.dec, -1); return changed(d.dec); }
    if (d.rm) { cart.remove(+d.rm); return changed(d.rm); }
    if (d.cat) { S.cat = d.cat; renderChips(); return renderMenu(); }
    if (d.reset !== undefined) { S.q = ''; S.cat = 'all'; $('#fsearch').value = ''; renderChips(); return renderMenu(); }
    if (t.id === 'rmGone') {
      const onMenu = new Set(menu.map(i => String(i.id))), flagged = new Set((S.quote ? S.quote.unavailable : []).map(u => String(u.item_id)));
      Object.keys(cart.get()).filter(id => !onMenu.has(id) || flagged.has(id)).forEach(id => cart.remove(+id));
      document.querySelectorAll('[data-ctl]').forEach(el => refreshControls(el.dataset.ctl)); return scheduleQuote();
    }
    if (t.id === 'checkout') return checkout();
    if (t.id === 'loginGo') sessionStorage.setItem('bmm_next', '#/food');
    if (t.id === 'fab') $('#cartPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  $('#view').onchange = e => { if (e.target.id === 'fb') { S.booking = e.target.value; S.idem = null; renderCart(); } };
  $('#fsearch').oninput = e => { S.q = e.target.value; renderMenu(); };
  renderChips(); renderMenu(); scheduleQuote();
}

// ---------- pay a food order (reuses POST /payments/pay) ----------
async function foodPayPage(id, notice){
  if (!need()) return; loading();
  const [o, cfg] = await Promise.all([api('/food/orders/' + id), api('/payments/config')]);
  if (o.payment_status === 'successful' && o.status === 'placed') { location.hash = '#/food/order/' + id; return; }
  if (o.status !== 'placed') return show(`<div class="empty">This food order is ${esc(o.status)}. <a href="#/food">Back to the menu</a></div>`);
  if (o.pay_with_booking) return show(`<div class="card"><h2>Pay together with your tickets</h2><p>This food order belongs to booking <b>${esc(o.booking_reference)}</b>, which is still awaiting payment. It is included in that payment.</p><a class="btn" href="#/pay/${o.booking_id}">Go to ticket payment</a></div>`);
  const pick = cfg.mode === 'selectable';
  show(`<a href="#/food">← Menu</a><h1>Pay for food order</h1><div class="two"><div class="card"><h2>Order ${esc(o.order_number)}</h2>
      <table class="totals">${foodItemRows(o.items.map(i => ({...i})))}${foodBillRows(o, o.rates)}</table>
      ${o.booking_reference ? `<p class="muted small">For booking ${esc(o.booking_reference)}. Your tickets are already paid; only this food is charged now.</p>` : ''}</div>
    <div class="card"><h2>Choose a payment method</h2>${notice ? `<p class="error small" role="alert">${esc(notice)}</p>` : ''}
      <div id="methods">${cfg.methods.map((m, i) => `<label class="opt"><span><input type="radio" name="pm" value="${esc(m.id)}" ${i === 0 ? 'checked' : ''}> ${esc(m.label)}</span></label>`).join('')}</div>
      ${pick ? `<label>Demo outcome</label><select id="sim"><option value="success">Simulate: payment succeeds</option><option value="failure">Simulate: payment fails</option></select>` : `<p class="muted small">Demo outcome is set by the server (${esc(cfg.mode)}).</p>`}
      <button class="btn block" id="payBtn" style="margin-top:12px">Pay ${inr(o.total)}</button>
      <button class="btn ghost block" id="cancelOrder" style="margin-top:8px">Cancel this order</button>
      <p class="notice">🔒 <b>Demo payment.</b> No real money moves. Do not enter card numbers, UPI PINs or bank passwords: this screen never asks for them.</p></div></div>`);
  let busy = false;
  $('#payBtn').onclick = async () => {
    if (busy) return; busy = true; $('#payBtn').disabled = true;
    const body = {food_order_id: +id, method: document.querySelector('input[name=pm]:checked').value}; if (pick) body.simulate = $('#sim').value;
    show(`<div class="card" style="text-align:center"><h2>Processing payment…</h2><div class="spinner" role="status"></div><p class="muted">Simulated payment in progress. Please do not refresh or press back.</p></div>`);
    const t0 = Date.now(); let res, error;
    try { res = await api('/payments/pay', {method: 'POST', body}); } catch (e) { error = e; }
    await sleep(Math.max(0, 1500 - (Date.now() - t0)));
    if (error) { toast(error.message, true); return foodPayPage(id, error.message); }
    if (res.outcome === 'failed') { toast('Payment failed. No money was charged.', true); return foodPayPage(id, (res.payment.failure_reason || 'The payment was not completed.') + ' No money was charged; you can try again.'); }
    location.hash = '#/food/order/' + id + '?paid=1';
  };
  $('#cancelOrder').onclick = async () => { if (!await confirmBox('Cancel this food order?')) return;
    try { await api(`/food/orders/${id}/cancel`, {method: 'POST'}); toast('Food order cancelled'); location.hash = '#/food'; } catch (e) { toast(e.message, true); } };
}

// ---------- confirmation ----------
async function foodOrderPage(id){
  if (!need()) return; loading();
  const o = await api('/food/orders/' + id);
  const paid = o.status === 'placed' && o.payment_status === 'successful', cancelled = o.status === 'cancelled';
  const p = o.pickup;
  show(`<a href="#/food/orders">← My food orders</a><div class="card result ${paid ? 'ok' : 'bad'}" style="margin-top:10px">
    <h1>${paid ? '✅ Food order confirmed' : cancelled ? 'Order cancelled' : o.payment_status === 'failed' ? '❌ Payment failed' : '⏳ Payment pending'}</h1>
    <p>${paid ? 'Your payment was successful. (Demo payment: no real money was charged.)' : cancelled ? 'This order was cancelled and will not be prepared.' : o.pay_with_booking ? 'This order will be paid together with your tickets.' : 'Complete the payment to confirm your order. Nothing is charged until then.'}</p>
    <div class="grid facts"><div><span class="muted small">Order ID</span><br><b>${esc(o.order_number)}</b> <span class="muted small">(#${o.id})</span></div>
      <div><span class="muted small">Payment status</span><br>${payPill(o.payment_status)}</div>
      ${o.transaction_id ? `<div><span class="muted small">Transaction ID</span><br><b>${esc(o.transaction_id)}</b></div>` : ''}
      ${o.paid_at ? `<div><span class="muted small">Paid at</span><br><b>${esc(fdt(o.paid_at))}</b></div>` : ''}
      ${o.booking_reference ? `<div><span class="muted small">Booking</span><br><a href="#/booking/${o.booking_id}"><b>${esc(o.booking_reference)}</b></a></div>` : ''}</div></div>
    <div class="two" style="margin-top:14px"><div class="card"><h2>Order summary</h2><table class="totals">${foodItemRows(o.items)}${foodBillRows(o, o.rates)}</table></div>
    ${p ? `<div class="card pickup"><h2>Pickup details</h2>${paid ? `<div class="pcode" aria-label="Pickup code">${esc(o.pickup_code)}</div>
        ${o.qr_image ? `<img class="pqr" src="${esc(o.qr_image)}" alt="QR code for food order ${esc(o.order_number)}" width="170" height="170">` : ''}` : '<p class="muted">Your pickup code and QR appear here once the order is paid.</p>'}
      ${p.stadium ? `<p><b>${esc(p.stadium)}, ${esc(p.location)}</b><br><span class="muted">${esc(p.match)} · ${dt(p.match_date)} · ${esc(p.timing)}</span></p>` : ''}
      <p class="muted small">${esc(p.instructions)}</p></div>` : ''}</div>
    <div class="row" style="margin-top:12px">${o.can_pay ? `<a class="btn" href="#/food/pay/${o.id}">Pay ${inr(o.total)}</a>` : ''}${o.pay_with_booking ? `<a class="btn" href="#/pay/${o.booking_id}">Pay with tickets</a>` : ''}
      ${o.receipt_available ? `<button class="btn" data-dl="/food/orders/${o.id}/receipt" data-name="${esc(o.order_number)}-receipt.pdf">Download receipt (PDF)</button>` : ''}
      ${o.can_cancel ? '<button class="btn danger" id="cancelOrder">Cancel order</button>' : ''}<a class="btn ghost" href="#/food">Order more food</a></div>`);
  wireDownloads(); const base = $('#view').onclick;
  $('#view').onclick = async e => { await base(e); if (e.target.id === 'cancelOrder') { if (!await confirmBox('Cancel this food order?')) return;
    try { await api(`/food/orders/${id}/cancel`, {method: 'POST'}); toast('Food order cancelled'); foodOrderPage(id); } catch (er) { toast(er.message, true); } } };
}

async function foodOrdersPage(){
  if (!need()) return; loading();
  const list = await api('/food/orders');
  show(`<a href="#/food">← Menu</a><h1>My food orders</h1>${table(['Order', 'Placed', 'Items', 'Total', 'Payment', ''], list.map(o => [`<b>${esc(o.order_number)}</b>${o.booking_reference ? `<div class="muted small">${esc(o.booking_reference)}</div>` : ''}`,
    esc(fdt(o.created_at)), esc(o.items.map(i => `${i.name} ×${i.quantity}`).join(', ')), inr(o.total),
    o.status === 'cancelled' ? '<span class="pill bad">CANCELLED</span>' : payPill(o.payment_status), `<a class="btn sm" href="#/food/order/${o.id}">View</a>`]), 'No food orders yet. <a href="#/food">Browse the menu</a>')}`);
}

if (typeof window !== 'undefined') window.addEventListener('storage', e => { if (e.key === 'bmm_cart') { if (typeof badge === 'function') badge(); if (window.BMM_foodCartChanged) window.BMM_foodCartChanged(); } });
if (typeof module !== 'undefined' && module.exports) module.exports = {makeCart, FOOD_MAX_QTY, foodEmoji, foodMatches, cartLinesFor};
