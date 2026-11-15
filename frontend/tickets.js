'use strict';
/* Demo payment, QR tickets, staff scanner and admin entry views.
   Loaded BEFORE app.js (it only declares functions); uses app.js helpers (api, show, esc, inr, dt, toast, need, state...) at call time. */
const SCANNER_LIB = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
const fdt = s => s ? new Date(s).toLocaleString('en-IN', {day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit'}) : '—';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const _timers = []; let _scanner = null;
const isStaff = () => state.user && ['staff', 'admin'].includes(state.user.role);

window.BMM_cleanup = function(){                 // called by the router before every page change
  _timers.splice(0).forEach(clearInterval);
  if (_scanner){ const s = _scanner; _scanner = null; s.stop().then(() => s.clear()).catch(() => {}); }
};

async function downloadFile(path, filename){
  const r = await fetch(API + path, {headers: {Authorization: 'Bearer ' + state.token}});
  if (!r.ok) throw new Error('Download failed (' + r.status + ')');
  const u = URL.createObjectURL(await r.blob()); const a = document.createElement('a');
  a.href = u; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(u), 5000);
}
const vPill = v => `<span class="pill ${v==='valid'?'ok':v==='used'?'warn':'bad'}">${esc(String(v).toUpperCase())}</span>`;
const payPill = s => `<span class="pill ${s==='successful'?'ok':s==='failed'?'bad':'warn'}">${s==='successful'?'SUCCESS':esc(String(s).toUpperCase())} (demo)</span>`;
function table(heads, rows, empty){
  return rows.length ? `<div class="tablewrap"><table><tr>${heads.map(h=>`<th>${esc(h)}</th>`).join('')}</tr>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</table></div>` : `<div class="empty">${empty}</div>`;
}
function wireDownloads(){                          // buttons with data-dl="<api path>" data-name="<file name>"
  $('#view').onclick = async e => {                // property (not addEventListener) so handlers never pile up across pages
    handleGo(e);
    const b = e.target.closest('[data-dl]'); if (!b) return;
    b.disabled = true; try { await downloadFile(b.dataset.dl, b.dataset.name); } catch(er){ toast(er.message, true); } b.disabled = false;
  };
}

// ---------- digital ticket ----------
function ticketCard(t){
  const live = t.validity === 'valid';
  return `<article class="ticket"><header class="ticket-head"><b>🏟️ BOOK MY MATCH</b><span>${esc(t.sport)}</span></header>
    <div class="ticket-body"><div class="ticket-info"><h3>${esc(t.match)}</h3><dl>
      <dt>Ticket ID</dt><dd><b>${esc(t.ticket_id)}</b></dd><dt>Booking ref</dt><dd>${esc(t.booking_reference)}</dd><dt>Customer</dt><dd>${esc(t.customer_name)}</dd>
      <dt>Stadium</dt><dd>${esc(t.stadium)}, ${esc(t.location)}</dd><dt>Date &amp; time</dt><dd>${dt(t.match_date)} · ${esc(t.timing)}</dd>
      <dt>Category</dt><dd>${esc(t.category)}</dd><dt>Seat</dt><dd>${esc(t.seat_number || 'Open seating (category ticket)')}</dd>
      <dt>Payment</dt><dd>${payPill(t.payment_status)}</dd><dt>Status</dt><dd>${vPill(t.validity)}${t.used_at?` <span class="muted small">entered ${esc(fdt(t.used_at))}</span>`:''}</dd></dl></div>
    <div class="ticket-qr">${live && t.qr_image ? `<img src="${esc(t.qr_image)}" alt="QR code for ticket ${esc(t.ticket_id)}" width="200" height="200">` : `<div class="stamp">${esc(t.validity.toUpperCase())}</div>`}
      ${live && t.backup_code ? `<div class="small muted">Backup code<br><code>${esc(t.backup_code)}</code></div>` : ''}</div></div>
    <footer class="ticket-foot">Demo ticket · no real money was charged · one entry per ticket</footer></article>`;
}

// ---------- payment ----------
async function payPage(id){
  if (!need()) return; loading();
  const [b, cfg] = await Promise.all([api('/bookings/' + id), api('/payments/config')]);
  if (b.status === 'confirmed') { location.hash = '#/payment/success/' + id; return; }
  if (b.status !== 'pending_payment') return show(`<div class="empty">This booking is ${esc(b.status.replace('_',' '))}. <a href="#/matches">Browse matches</a></div>`);
  const pick = cfg.mode === 'selectable';
  show(`<h1>Payment</h1><div class="two"><div class="card"><h2>Order summary</h2>
    <p><b>${esc(b.match)}</b><br><span class="muted">${esc(b.sport)} · ${esc(b.stadium)}, ${esc(b.location)}<br>${dt(b.match_date)} · ${esc(b.timing)}</span></p>
    <table class="totals">${b.booking_type==='seat'?b.seats.map(x=>`<tr><td>${esc(x.stand)} · ${esc(x.section)} · Row ${esc(x.row)} · Seat ${x.number}<br><span class="muted small">${esc(x.category)}</span></td><td align="right">${inr(x.price)}</td></tr>`).join(''):`<tr><td>${b.quantity} × ${esc(b.category)} @ ${inr(b.unit_price)}</td><td align="right">${inr(b.subtotal)}</td></tr>`}
      <tr><td>Convenience fee</td><td align="right">${inr(b.fee)}</td></tr><tr><td>Taxes</td><td align="right">${inr(b.tax)}</td></tr>
      ${b.food_total?`<tr><td><b>Food orders</b></td><td align="right">${inr(b.food_total)}</td></tr>${bookingFoodRows(b.food_orders)}`:''}
      <tr class="grand"><td>Total payable</td><td align="right">${inr(b.grand_total)}</td></tr></table>
    <p class="notice" id="hold">Your ${b.booking_type==='seat'?'seats are':'tickets are'} reserved for <b id="cd">…</b></p>${b.booking_type==='seat'?'<button class="btn danger ghost" id="cancelCheckout">Cancel checkout and release seats</button>':''}</div>
    <div class="card"><h2>Choose a payment method</h2><div id="methods">${cfg.methods.map((m,i)=>`<label class="opt"><span><input type="radio" name="pm" value="${esc(m.id)}" ${i===0?'checked':''}> ${esc(m.label)}</span></label>`).join('')}</div>
      ${pick?`<label>Demo outcome</label><select id="sim"><option value="success">Simulate: payment succeeds</option><option value="failure">Simulate: payment fails</option></select>`:`<p class="muted small">Demo outcome is set by the server (${esc(cfg.mode)}).</p>`}
      <button class="btn" id="payBtn" style="width:100%;margin-top:12px">Pay ${inr(b.grand_total)}</button>
      <p class="notice">🔒 <b>Demo payment.</b> No real money moves. Do not enter card numbers, UPI PINs or bank passwords: this screen never asks for them.</p></div></div>`);
  const end = Date.now() + (b.expires_in_seconds || 0) * 1000, btn = $('#payBtn');   // server-computed: independent of the browser's timezone
  const tick = () => { const s = Math.max(0, Math.floor((end - Date.now()) / 1000)); $('#cd').textContent = Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
    if (!s){ $('#hold').innerHTML = '⏰ Reservation expired. The tickets were released.'; btn.disabled = true; } };
  tick(); _timers.push(setInterval(tick, 1000));
  const cc = $('#cancelCheckout'); if (cc) cc.onclick = async () => { if (!await confirmBox('Cancel this checkout and release your seats?', {title: 'Release seats', yes: 'Release seats', danger: true})) return;
    try { await api(`/bookings/${id}/cancel`, {method: 'POST'}); toast('Checkout cancelled, seats released'); location.hash = '#/matches'; } catch(e){ toast(e.message, true); } };
  let busy = false;
  btn.onclick = async () => {
    if (busy) return; busy = true; btn.disabled = true; _timers.splice(0).forEach(clearInterval);  // stop the countdown before the page is replaced
    const method = document.querySelector('input[name=pm]:checked').value, body = {booking_id: +id, method};
    if (pick) body.simulate = $('#sim').value;
    show(`<div class="card" style="text-align:center"><h2>Processing payment…</h2><div class="spinner" role="status"></div><p class="muted">Simulated payment in progress. Please do not refresh or press back.</p></div>`);
    const t0 = Date.now(); let res, error;
    try { res = await api('/payments/pay', {method: 'POST', body}); } catch(e){ error = e; }
    await sleep(Math.max(0, 1800 - (Date.now() - t0)));
    if (error){ toast(error.message, true); return payPage(id); }
    location.hash = res.outcome === 'failed' ? `#/payment/failed/${id}?txn=${encodeURIComponent(res.payment.transaction_id)}` : '#/payment/success/' + id;
  };
}

async function paymentResultPage(kind, id, p){
  if (!need()) return; loading();
  if (kind === 'success'){
    const b = await api('/bookings/' + id);
    if (b.status !== 'confirmed') return show(`<div class="empty">This booking is not confirmed yet. <a href="#/pay/${esc(id)}">Go to payment</a></div>`);
    const tl = await api(`/bookings/${id}/tickets`);
    show(`<div class="card result ok"><h1>✅ Payment successful</h1><p>Your booking is <b>confirmed</b>. (Demo payment: no real money was charged.)</p>
      <div class="grid facts"><div><span class="muted small">Booking reference</span><br><b>${esc(b.reference)}</b></div><div><span class="muted small">Transaction ID</span><br><b>${esc(b.transaction_id)}</b></div>
      <div><span class="muted small">Paid at</span><br><b>${esc(fdt(b.paid_at))}</b></div><div><span class="muted small">Amount paid</span><br><b>${inr(b.paid_amount ?? b.grand_total)}</b></div>
      <div><span class="muted small">Tickets booked</span><br><b>${b.quantity}</b></div></div>
      <div class="row" style="margin-top:12px"><button class="btn" data-dl="/bookings/${esc(id)}/tickets/pdf" data-name="${esc(b.reference)}-tickets.pdf">Download Ticket PDF</button><a class="btn ghost" href="#/tickets">View My Tickets</a></div></div>
      ${(b.food_orders||[]).filter(o=>o.status==='placed').length?`<div class="card" style="margin-top:14px"><h2>🍴 Your food</h2>${(b.food_orders||[]).filter(o=>o.status==='placed').map(o=>`<div class="row between"><span><b>${esc(o.order_number)}</b> · ${o.items.map(i=>esc(i.name)+' ×'+i.quantity).join(', ')} · ${inr(o.total)}${o.pickup_code?` · pickup code <b>${esc(o.pickup_code)}</b>`:''}</span><a class="btn ghost sm" href="#/food/order/${o.id}">${o.payment_status==='successful'?'Pickup &amp; receipt':'View'}</a></div>`).join('')}</div>`:''}
      <h2 style="margin-top:20px">Your tickets</h2><div class="tickets">${tl.map(t => ticketCard(t) + `<div class="row end" style="margin:6px 0 18px"><button class="btn ghost sm" data-dl="/tickets/${esc(t.ticket_id)}/pdf" data-name="${esc(t.ticket_id)}.pdf">Download Individual Ticket</button></div>`).join('')}</div>`);
    wireDownloads();
  } else {
    const txn = p.get('txn'); const [b, pay] = await Promise.all([api('/bookings/' + id), txn ? api('/payments/' + encodeURIComponent(txn)).catch(() => null) : null]);
    const live = b.status === 'pending_payment';
    show(`<div class="card result bad"><h1>❌ Payment failed</h1><p>${esc(pay ? pay.failure_reason : 'The payment was not completed.')} <b>No money was charged and no tickets were issued.</b></p>
      <div class="grid facts">${pay?`<div><span class="muted small">Transaction ID</span><br><b>${esc(pay.transaction_id)}</b></div><div><span class="muted small">Attempted at</span><br><b>${esc(fdt(pay.completed_at))}</b></div><div><span class="muted small">Method</span><br><b>${esc(pay.method_label)}</b></div>`:''}
      <div><span class="muted small">Booking reference</span><br><b>${esc(b.reference)}</b></div></div>
      ${live?`<p class="notice">Your tickets are still held until <b>${esc(fdt(b.expires_at))}</b>.</p>`:`<p class="notice">This booking is ${esc(b.status.replace('_',' '))}.</p>`}
      <div class="row" style="margin-top:12px">${live?`<a class="btn" href="#/pay/${esc(id)}">Try again</a><button class="btn danger" id="cancelHold">Cancel booking</button>`:'<a class="btn" href="#/matches">Browse matches</a>'}<a class="btn ghost" href="#/bookings">My bookings</a></div></div>`);
    const c = $('#cancelHold'); if (c) c.onclick = async () => { if (!await confirmBox('Cancel this booking and release the tickets?')) return;
      try { await api(`/bookings/${id}/cancel`, {method: 'POST'}); toast('Booking cancelled'); location.hash = '#/bookings'; } catch(e){ toast(e.message, true); } };
  }
}

// ---------- My Tickets ----------
async function ticketsPage(p){
  if (!need()) return; loading();
  const tab = p.get('tab') || 'upcoming';
  const [tix, bookings] = await Promise.all([api('/tickets?scope=all'), tab === 'history' ? api('/bookings') : []]);
  const tabs = [['upcoming','Upcoming'],['completed','Completed'],['history','Booking history']];
  let body;
  if (tab === 'history'){
    body = table(['Reference','Match','Date','Tickets','Total','Booking','Payment',''], bookings.map(b => [esc(b.reference), esc(b.match), dt(b.match_date), `${b.quantity} × ${esc(b.category)}`, inr(b.grand_total),
      `<span class="pill ${b.status==='confirmed'?'ok':b.status==='pending_payment'?'warn':'bad'}">${esc(b.status.replace('_',' '))}</span>`, payPill(b.payment_status), `<a href="#/booking/${b.id}">Details</a>`]), 'No bookings yet.');
  } else {
    const list = tix.filter(t => t.phase === tab);
    const groups = {}; list.forEach(t => (groups[t.booking_reference] = groups[t.booking_reference] || []).push(t));
    body = list.length ? Object.entries(groups).map(([ref, ts]) => `<div class="card" style="margin-bottom:12px"><div class="row between"><b>${esc(ts[0].match)}</b>
      <button class="btn ghost sm" data-dl="/bookings/${ts[0].booking_id}/tickets/pdf" data-name="${esc(ref)}-tickets.pdf">Download all (PDF)</button></div>
      <div class="muted small">${esc(ts[0].stadium)} · ${dt(ts[0].match_date)} · ${esc(ts[0].timing)} · booking ${esc(ref)}</div>
      ${table(['Ticket','Category','Ticket status','Payment',''], ts.map(t => [`<a href="#/ticket/${esc(t.ticket_id)}">${esc(t.ticket_id)}</a>`, esc(t.category), vPill(t.validity) + (t.used_at?`<div class="muted small">entered ${esc(fdt(t.used_at))}</div>`:''), payPill(t.payment_status),
        `<a class="btn sm" href="#/ticket/${esc(t.ticket_id)}">View QR</a> <button class="btn ghost sm" data-dl="/tickets/${esc(t.ticket_id)}/pdf" data-name="${esc(t.ticket_id)}.pdf">PDF</button>`]), '')}</div>`).join('')
      : `<div class="empty">No ${tab} tickets. <a href="#/matches">Find a match</a></div>`;
  }
  show(`<h1>My Tickets</h1><div class="tabs">${tabs.map(([k,l]) => `<button data-go="#/tickets?tab=${k}" class="${k===tab?'on':''}">${l}</button>`).join('')}</div>${body}`);
  wireDownloads();
}
async function ticketPage(tid){
  if (!need()) return; loading(); const t = await api('/tickets/' + encodeURIComponent(tid));
  show(`<a href="#/tickets">← My tickets</a><div style="margin-top:10px">${ticketCard(t)}</div>
    <div class="row" style="margin-top:12px"><button class="btn" data-dl="/tickets/${esc(t.ticket_id)}/pdf" data-name="${esc(t.ticket_id)}.pdf">Download ticket (PDF)</button><a class="btn ghost" href="#/booking/${t.booking_id}">Booking details</a></div>`);
  wireDownloads();
}

// ---------- staff / admin QR scanner ----------
function loadScannerLib(){
  return new Promise((res, rej) => { if (window.Html5Qrcode) return res(); const s = document.createElement('script'); s.src = SCANNER_LIB; s.onload = res;
    s.onerror = () => rej(new Error('Could not load the QR scanner library. Check the internet connection, or use manual entry below.')); document.head.append(s); });
}
async function scannerPage(){
  if (!isStaff()) return show('<div class="error">Staff access required. <a href="#/login">Log in</a> with a staff or admin account.</div>');
  loading(); const [ms, recent] = await Promise.all([api('/staff/matches'), api('/staff/recent-scans?limit=8')]);
  show(`<h1>Entry scanner</h1><div class="two"><div class="card"><label>This gate is for</label>
      <select id="gm" style="width:100%"><option value="">Any match (no match check)</option>${ms.map(m=>`<option value="${m.id}">${esc(m.title)} · ${dt(m.date)} · ${esc(m.stadium.name)}</option>`).join('')}</select>
      <div id="reader" class="reader"></div><p id="camMsg" class="muted small">Press “Start camera” and allow camera access. Camera scanning needs HTTPS (or localhost).</p>
      <div class="row"><button class="btn" id="camStart">Start camera</button><button class="btn ghost" id="camStop" hidden>Stop camera</button></div>
      <label>Manual entry (if a QR will not scan)</label><form id="mf" class="row"><input id="mt" placeholder="Backup code printed under the QR" autocomplete="off" maxlength="200" style="flex:1;min-width:200px"><button class="btn">Verify</button></form></div>
    <div><div id="res" class="card scanres idle"><h2>Waiting for a ticket…</h2><p class="muted">Results appear here instantly.</p></div></div></div>
    <h2 style="margin-top:20px">Your recent scans</h2><div id="recent">${recentTable(recent)}</div>`);
  let busy = false, lastText = '', lastAt = 0;
  const verify = async text => {
    if (busy) return; busy = true;
    try { const r = await api('/staff/verify', {method: 'POST', body: {token: String(text).slice(0, 200), expected_match_id: $('#gm').value ? +$('#gm').value : null}});
      renderScan(r); if (navigator.vibrate) navigator.vibrate(r.ok ? 80 : [120, 60, 120]);
      $('#recent').innerHTML = recentTable(await api('/staff/recent-scans?limit=8')); }
    catch(e){ $('#res').className = 'card scanres err'; $('#res').innerHTML = `<h2>⚠️ Could not verify</h2><p>${esc(e.message)}</p>`; }
    finally { busy = false; }
  };
  $('#mf').onsubmit = e => { e.preventDefault(); const v = $('#mt').value.trim(); if (v) { verify(v); $('#mt').value = ''; } };
  $('#camStart').onclick = async () => {
    try {
      await loadScannerLib(); _scanner = new Html5Qrcode('reader');
      await _scanner.start({facingMode: 'environment'}, {fps: 10, qrbox: {width: 240, height: 240}}, text => {
        const now = Date.now(); if (text === lastText && now - lastAt < 4000) return; lastText = text; lastAt = now; verify(text); }, () => {});
      $('#camStart').hidden = true; $('#camStop').hidden = false; $('#camMsg').textContent = 'Camera on. Point it at the ticket QR code.';
    } catch(e){ _scanner = null;
      const m = String(e && (e.message || e)); $('#camMsg').innerHTML = '⚠️ ' + esc(/denied|NotAllowed|Permission/i.test(m) ? 'Camera permission was denied. Allow it in the browser settings, or use manual entry.'
        : /NotFound|no camera/i.test(m) ? 'No camera was found. Use manual entry.' : /secure|https/i.test(m) ? 'Camera access needs HTTPS. Use the https:// address of the site.' : m); }
  };
  $('#camStop').onclick = () => { window.BMM_cleanup(); $('#camStart').hidden = false; $('#camStop').hidden = true; $('#camMsg').textContent = 'Camera stopped.'; };
}
function renderScan(r){
  const cls = r.result === 'success' ? 'ok' : r.result === 'already_used' ? 'warn' : 'err', t = r.ticket;
  $('#res').className = 'card scanres ' + cls;
  $('#res').innerHTML = `<h1>${r.result==='success'?'✅':r.result==='already_used'?'⚠️':'⛔'} ${esc(r.message)}</h1>` + (t ? `<dl class="kv"><dt>Ticket</dt><dd>${esc(t.ticket_id)}</dd><dt>Holder</dt><dd>${esc(t.customer_name)}</dd>
      <dt>Booking</dt><dd>${esc(t.booking_reference)}</dd><dt>Match</dt><dd>${esc(t.match)}</dd><dt>Stadium</dt><dd>${esc(t.stadium)}</dd><dt>Category</dt><dd>${esc(t.category)}</dd>${t.seat_number?`<dt>Seat</dt><dd><b>${esc(t.seat_number)}</b></dd>`:''}
      <dt>Date</dt><dd>${dt(t.match_date)} · ${esc(t.timing)}</dd></dl>` : '<p>This code does not match any ticket.</p>')
    + (r.previous_entry ? `<p><b>Already entered:</b> ${esc(fdt(r.previous_entry.used_at))} · verified by ${esc(r.previous_entry.verified_by)}</p>` : '')
    + (r.ok ? `<p>Entry recorded at ${esc(fdt(r.scanned_at))}.</p>` : '<p><b>Do not admit.</b></p>');
}
const RESULT_LABEL = {success:'Entry allowed', already_used:'Already used', invalid:'Invalid', cancelled:'Cancelled', unpaid:'Not paid', wrong_match:'Wrong match', not_match_day:'Not match day'};
const recentTable = rows => table(['Time','Ticket','Result'], rows.map(r => [esc(fdt(r.scanned_at)), esc(r.ticket_id || '—'), `<span class="pill ${r.result==='success'?'ok':r.result==='already_used'?'warn':'bad'}">${esc(RESULT_LABEL[r.result]||r.result)}</span>`]), 'No scans yet.');

// ---------- admin: entry & tickets ----------
async function adminEntryTab(p, head){
  const view = p.get('view') || 'stats';
  const sub = [['stats','Attendance statistics'],['search','Search tickets'],['list','Match-wise tickets'],['history','Entry history']];
  const nav = `${head}<div class="tabs sub">${sub.map(([k,l]) => `<button data-go="#/admin?tab=entry&view=${k}" class="${k===view?'on':''}">${l}</button>`).join('')}<button data-go="#/scanner">📷 Open QR scanner</button></div>`;
  const matchOpts = async sel => (await api('/matches?include_past=true&status=&limit=100')).map(m => `<option value="${m.id}" ${sel==m.id?'selected':''}>${esc(m.title)} · ${esc(m.date)}</option>`).join('');
  if (view === 'stats'){
    const s = await api('/admin/ticket-stats'), t = s.totals;
    return show(nav + `<div class="grid">${[['Tickets sold',t.sold],['Valid (not yet entered)',t.valid],['Used (entered)',t.used],['Cancelled',t.cancelled],['Remaining for sale',t.remaining_for_sale],['Held, unpaid',t.held_unpaid],['Attendance',t.attendance_percent+'%']].map(([k,v])=>`<div class="card"><div class="muted">${k}</div><div class="stat">${v}</div></div>`).join('')}</div>
      <h3 style="margin-top:18px">Match-wise attendance</h3>${table(['Match','Date','Sold','Valid','Used','Cancelled','Remaining','Held','Attendance'], s.matches.map(r => [esc(r.match), esc(r.match_date), r.sold, r.valid, r.used, r.cancelled, r.remaining_for_sale, r.held_unpaid, r.attendance_percent + '%']), 'No tickets issued yet.')}
      <p class="muted small">Sold = issued minus cancelled. Valid = sold and not yet used. Remaining = still on sale (excludes unpaid holds).</p>`);
  }
  const ticketRows = ts => table(['Ticket','Booking','Holder','Match','Category','Status','Entered','Verified by'], ts.map(t => [`<a href="#/admin?tab=entry&view=search&q=${encodeURIComponent(t.ticket_id)}">${esc(t.ticket_id)}</a>`, esc(t.booking_reference), esc(t.customer_name), esc(t.match), esc(t.category), vPill(t.validity), esc(fdt(t.used_at)), esc(t.verified_by || '—')]), 'No tickets found.');
  if (view === 'search'){
    const q = p.get('q') || '', res = q ? await api('/admin/tickets?q=' + encodeURIComponent(q)) : [];
    let detail = ''; if (res.length === 1){ const d = await api('/admin/tickets/' + encodeURIComponent(res[0].ticket_id));
      detail = `<h3>Entry history for ${esc(d.ticket_id)}</h3>` + table(['Time','Result','Scanned by'], d.entry_history.map(h => [esc(fdt(h.scanned_at)), esc(RESULT_LABEL[h.result]||h.result), esc(h.scanned_by)]), 'No scan attempts yet.'); }
    show(nav + `<form id="sf" class="filters"><input name="q" value="${esc(q)}" placeholder="Ticket ID or booking reference" required><button class="btn">Search</button></form>${q?ticketRows(res):''}${detail}`);
    $('#sf').onsubmit = e => { e.preventDefault(); location.hash = '#/admin?tab=entry&view=search&q=' + encodeURIComponent(new FormData(e.target).get('q')); };
    return;
  }
  if (view === 'list'){
    const mid = p.get('match_id') || '', st = p.get('status') || '';
    const res = await api('/admin/tickets?limit=200' + (mid ? '&match_id=' + encodeURIComponent(mid) : '') + (st ? '&status=' + encodeURIComponent(st) : ''));
    show(nav + `<form id="lf" class="filters"><select name="match_id"><option value="">All matches</option>${await matchOpts(mid)}</select><select name="status">${[['','All tickets'],['valid','Unused (valid)'],['used','Used'],['cancelled','Cancelled']].map(([v,l])=>`<option value="${v}" ${st===v?'selected':''}>${l}</option>`).join('')}</select><button class="btn">Show</button></form>${ticketRows(res)}<p class="muted small">Showing up to 200 tickets.</p>`);
    $('#lf').onsubmit = e => { e.preventDefault(); const f = new URLSearchParams(); new FormData(e.target).forEach((v,k) => v && f.set(k, v)); location.hash = '#/admin?tab=entry&view=list&' + f; };
    return;
  }
  const mid = p.get('match_id') || '', rs = p.get('result') || '';
  const logs = await api('/admin/entry-logs?limit=200' + (mid ? '&match_id=' + encodeURIComponent(mid) : '') + (rs ? '&result=' + encodeURIComponent(rs) : ''));
  show(nav + `<form id="hf" class="filters"><select name="match_id"><option value="">All matches</option>${await matchOpts(mid)}</select><select name="result"><option value="">All results</option>${Object.entries(RESULT_LABEL).map(([k,l])=>`<option value="${k}" ${rs===k?'selected':''}>${l}</option>`).join('')}</select><button class="btn">Show</button></form>
    ${table(['Time','Result','Ticket','Booking','Scanned by'], logs.map(l => [esc(fdt(l.scanned_at)), `<span class="pill ${l.result==='success'?'ok':l.result==='already_used'?'warn':'bad'}">${esc(RESULT_LABEL[l.result]||l.result)}</span>`, esc(l.ticket_id || '—'), esc(l.booking_reference || '—'), esc(l.scanned_by)]), 'No scan attempts yet.')}`);
  $('#hf').onsubmit = e => { e.preventDefault(); const f = new URLSearchParams(); new FormData(e.target).forEach((v,k) => v && f.set(k, v)); location.hash = '#/admin?tab=entry&view=history&' + f; };
}
