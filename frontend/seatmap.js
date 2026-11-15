'use strict';
/* Phase 3B-3D: interactive seat selection for matches that have a seating layout, and seat helpers for bookings.
   All data (layout, availability, prices, totals) comes from the backend; nothing here decides a price or an availability.
   Pure helpers first (unit-tested in Node), page code below. */
const SEAT_STATE = {
  available: {label: 'Available', cls: 'available'}, mine: {label: 'Selected by you', cls: 'selected'}, selected: {label: 'Selected by you', cls: 'selected'},
  held: {label: 'Temporarily held by another user', cls: 'held'}, booked: {label: 'Already booked', cls: 'booked'}, unavailable: {label: 'Unavailable', cls: 'unavailable'},
};
const seatStateOf = (seat, selected) => (selected ? 'selected' : (SEAT_STATE[seat.status] ? seat.status : 'unavailable'));
function canToggleSeat(seat, isSelected, count, max){
  if (isSelected) return {ok: true, action: 'remove'};
  if (!seat.selectable) return {ok: false, reason: seat.status === 'booked' ? 'That seat is already booked' : seat.status === 'held' ? 'That seat is being held by another user' : 'That seat is not available'};
  if (count >= max) return {ok: false, reason: `You can select at most ${max} seats`};
  return {ok: true, action: 'add'};
}
function seatAria(seat, row, selected){
  const st = seatStateOf(seat, selected);
  return `Row ${row.label}, seat ${seat.number}, ${seat.category}, ${seat.price} rupees, ${SEAT_STATE[st].label.toLowerCase()}`;
}
/* [{stand, section, row, number}] -> "North Stand · Block A · Row A1: seats 1, 2, 3" lines (used in bookings and the payment page) */
function seatSummaryLines(seats){
  const groups = new Map();
  for (const s of seats || []) { const k = `${s.stand} · ${s.section} · Row ${s.row}`; (groups.get(k) || groups.set(k, []).get(k)).push(s.number); }
  return [...groups].map(([k, nums]) => `${k}: seat${nums.length > 1 ? 's' : ''} ${nums.sort((a, b) => a - b).join(', ')}`);
}
const seatBookingText = b => (b.booking_type === 'seat' ? `${b.quantity} seat${b.quantity > 1 ? 's' : ''} (${b.category})` : `${b.quantity} × ${b.category}`);
const seatEsc2 = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const SEAT_LEGEND = ['available', 'selected', 'held', 'booked', 'unavailable'];
const seatLegendHtml = () => `<ul class="seat-legend" aria-label="Seat colour legend">${SEAT_LEGEND.map(k => `<li><span class="seat ${SEAT_STATE[k].cls} lg" aria-hidden="true"></span>${seatEsc2(SEAT_STATE[k].label)}</li>`).join('')}</ul>`;

async function seatPage(id){
  loading();
  const [m, map] = await Promise.all([api('/matches/' + id), api(`/matches/${id}/seating`)]);
  if (!map.seating_enabled) return show(`<a class="back" href="#/match/${seatEsc2(id)}">← Back to match</a>${emptyState({icon: '🎟️', title: 'Seat selection is not available for this match', text: 'You can book category tickets on the match page.', actionHtml: `<a class="btn" href="#/match/${seatEsc2(id)}">Back to match</a>`})}`);
  const MAX = map.max_seats || 10, sel = new Map();          // seat_id -> {seat_key, label, category, price}
  const S = {stand: map.stands.find(s => s.sections.length)?.id, section: null, data: null, quote: null, busy: false, loadingQuote: 0};
  const stand = () => map.stands.find(s => s.id === S.stand);
  S.section = stand()?.sections[0]?.id ?? null;
  const key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random())).replace(/-/g, '');
  show(`<a class="back" href="#/match/${seatEsc2(id)}">← Back to match</a><h1>Select your seats</h1>
    <p class="muted">${seatEsc2(m.team_a)} vs ${seatEsc2(m.team_b)} · ${dt(m.date)} · ${seatEsc2(m.timing)} · ${seatEsc2(m.stadium.name)}</p>
    <div class="two"><div><div class="card"><div id="stands" class="row" role="tablist" aria-label="Stands"></div><div id="secs" class="row" style="margin-top:8px"></div></div>
      <div class="card" style="margin-top:12px"><div class="row between"><h2 id="secTitle" style="margin:0">Seats</h2><button class="btn sm ghost" id="refresh">↻ Refresh</button></div>${seatLegendHtml()}
        <div class="stage" aria-hidden="true">PITCH / STAGE</div><div id="seatgrid" class="seatgrid" aria-live="polite"></div></div></div>
      <aside class="card" id="sum" aria-live="polite"></aside></div>`);

  function standsHtml(){
    $('#stands').innerHTML = map.stands.map(s => `<button class="btn sm ${s.id === S.stand ? '' : 'ghost'}" role="tab" aria-selected="${s.id === S.stand}" data-stand="${s.id}" ${s.sections.length ? '' : 'disabled'}>${seatEsc2(s.name)} <span class="muted small">(${s.available} free)</span></button>`).join('');
    $('#secs').innerHTML = (stand()?.sections || []).map(x => `<button class="btn sm ${x.id === S.section ? '' : 'ghost'}" data-section="${x.id}" aria-pressed="${x.id === S.section}">${seatEsc2(x.name)} · ${seatEsc2(x.category)} · ${inr(x.base_price)} <span class="muted small">(${x.available} free)</span></button>`).join('') || '<span class="muted">No sections.</span>';
  }
  function gridHtml(){
    const d = S.data; if (!d) return;
    $('#secTitle').textContent = d.section.name;
    $('#seatgrid').innerHTML = d.rows.map(r => `<div class="seatrow"><span class="rowlabel" aria-hidden="true">${seatEsc2(r.label)}</span><div class="seats" role="group" aria-label="Row ${seatEsc2(r.label)}">${r.seats.map(s => {
      const on = sel.has(s.seat_id), st = seatStateOf(s, on);
      return `<button type="button" class="seat ${SEAT_STATE[st].cls}" data-seat="${s.seat_id}" data-row="${seatEsc2(r.label)}" aria-pressed="${on}" ${s.selectable || on ? '' : 'aria-disabled="true"'} aria-label="${seatEsc2(seatAria(s, r, on))}" title="${seatEsc2(`Seat ${s.number} · ${s.category} · ${s.price}`)}">${s.number}</button>`; }).join('')}</div></div>`).join('') || '<p class="muted">No seats in this section.</p>';
  }
  function sumHtml(){
    const items = [...sel.entries()], q = S.quote;
    $('#sum').innerHTML = `<h2>Your seats</h2>${items.length ? `<ul class="selist">${items.map(([sid, x]) => `<li><span><b>${seatEsc2(x.label)}</b><br><span class="muted small">${seatEsc2(x.category)} · ${inr(x.price)}</span></span><button class="btn sm ghost" data-remove="${sid}" aria-label="Remove ${seatEsc2(x.label)}">✕</button></li>`).join('')}</ul>` : '<p class="muted">Pick available (green) seats on the map. You can choose up to ' + MAX + '.</p>'}
      <p class="muted small">${items.length} of ${MAX} seats selected</p>
      ${q && items.length ? `<table class="totals"><tr><td>${q.quantity} seat${q.quantity > 1 ? 's' : ''}</td><td align="right">${inr(q.subtotal)}</td></tr><tr><td>Booking fee</td><td align="right">${inr(q.fee)}</td></tr><tr><td>Tax</td><td align="right">${inr(q.tax)}</td></tr><tr class="grand"><td>Total</td><td align="right">${inr(q.total)}</td></tr></table>` : (items.length ? '<div class="muted small">Calculating price…</div>' : '')}
      <button class="btn" id="go" style="width:100%;margin-top:10px" ${items.length && q && !S.busy ? '' : 'disabled'}>${S.busy ? 'Reserving…' : 'Reserve seats and continue'}</button>
      <p class="notice">Seats are held for you for a few minutes while you pay. Prices are calculated by the server. Demo mode: no real payment is taken.</p>`;
  }
  async function loadSection(){
    if (S.section == null) { S.data = null; $('#seatgrid').innerHTML = '<p class="muted">Choose a section.</p>'; return; }
    $('#seatgrid').innerHTML = '<div class="spinner" role="status" aria-label="Loading seats"></div>';
    try { S.data = await api(`/matches/${id}/seating/sections/${S.section}`); } catch (e) { $('#seatgrid').innerHTML = errorState(e.message); return; }
    // anything we had selected here that became unavailable meanwhile is dropped, with an explanation
    let dropped = 0;
    for (const r of S.data.rows) for (const s of r.seats) if (sel.has(s.seat_id) && s.status !== 'available' && s.status !== 'mine') { sel.delete(s.seat_id); dropped++; }
    if (dropped) { toast(`${dropped} selected seat${dropped > 1 ? 's were' : ' was'} taken by someone else and removed`, true); requote(); }
    gridHtml();
  }
  async function requote(){
    const n = ++S.loadingQuote; S.quote = null; sumHtml();
    if (!sel.size) return;
    try { const q = await api(`/matches/${id}/seat-quote`, {method: 'POST', body: {seat_ids: [...sel.keys()]}}); if (n === S.loadingQuote) { S.quote = q; sumHtml(); } }
    catch (e) { if (n === S.loadingQuote) { toast(e.message, true); await loadSection(); } }
  }
  async function refreshAll(){ try { const fresh = await api(`/matches/${id}/seating`); map.stands = fresh.stands; standsHtml(); } catch {} await loadSection(); }

  $('#view').onclick = async e => {
    const t = e.target.closest('button'); if (!t) return handleGo(e);
    if (t.dataset.stand) { S.stand = +t.dataset.stand; S.section = stand().sections[0]?.id ?? null; standsHtml(); return loadSection(); }
    if (t.dataset.section) { S.section = +t.dataset.section; standsHtml(); return loadSection(); }
    if (t.id === 'refresh') return refreshAll();
    if (t.dataset.remove) { sel.delete(+t.dataset.remove); gridHtml(); return requote(); }
    if (t.dataset.seat) {
      const sid = +t.dataset.seat, row = S.data.rows.find(r => r.label === t.dataset.row), seat = row.seats.find(s => s.seat_id === sid);
      const c = canToggleSeat(seat, sel.has(sid), sel.size, MAX); if (!c.ok) return toast(c.reason, true);
      if (c.action === 'remove') sel.delete(sid); else sel.set(sid, {seat_key: seat.seat_key, category: seat.category, price: seat.price, label: `${S.data.section.name} · Row ${row.label} · Seat ${seat.number}`});
      gridHtml(); return requote();
    }
    if (t.id === 'go') {
      if (!state.user) { toast('Please log in to reserve seats', true); sessionStorage.setItem('bmm_next', location.hash); return location.hash = '#/login'; }
      S.busy = true; sumHtml();
      try { const b = await api('/bookings/seats', {method: 'POST', headers: {'Idempotency-Key': key}, body: {match_id: +id, seat_ids: [...sel.keys()]}});
        toast(`Seats reserved (${b.reference}). Complete payment before the timer runs out.`); location.hash = '#/pay/' + b.id; }
      catch (er) { S.busy = false; toast(er.message, true); await refreshAll(); await requote(); }
    }
  };
  standsHtml(); sumHtml(); await loadSection();
  _timers.push(setInterval(() => { if (!S.busy) loadSection(); }, 20000));   // pick up seats other users hold or book (cleared on navigation)
}

if (typeof module !== 'undefined' && module.exports) module.exports = {SEAT_STATE, seatStateOf, canToggleSeat, seatAria, seatSummaryLines, seatBookingText, seatLegendHtml, SEAT_LEGEND};
