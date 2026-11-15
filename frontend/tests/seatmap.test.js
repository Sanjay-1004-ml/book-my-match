// Run: node --test frontend/tests/seatmap.test.js
'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {seatStateOf, canToggleSeat, seatAria, seatSummaryLines, seatBookingText, seatLegendHtml} = require('../seatmap.js');

test('seat states map to the five colours and selected wins over available', () => {
  assert.equal(seatStateOf({status: 'available'}, false), 'available'); assert.equal(seatStateOf({status: 'available'}, true), 'selected');
  assert.equal(seatStateOf({status: 'mine'}, false), 'mine'); assert.equal(seatStateOf({status: 'held'}, false), 'held');
  assert.equal(seatStateOf({status: 'booked'}, false), 'booked'); assert.equal(seatStateOf({status: 'weird'}, false), 'unavailable');
  const lg = seatLegendHtml(); for (const w of ['available', 'selected', 'held', 'booked', 'unavailable', 'Already booked', 'Temporarily held']) assert.ok(lg.includes(w), w);
});
test('unavailable seats cannot be selected and the maximum is enforced', () => {
  const ok = {selectable: true, status: 'available'};
  assert.deepEqual(canToggleSeat(ok, false, 0, 10), {ok: true, action: 'add'}); assert.deepEqual(canToggleSeat(ok, true, 3, 10), {ok: true, action: 'remove'});
  assert.match(canToggleSeat({selectable: false, status: 'booked'}, false, 0, 10).reason, /already booked/);
  assert.match(canToggleSeat({selectable: false, status: 'held'}, false, 0, 10).reason, /another user/);
  assert.match(canToggleSeat(ok, false, 10, 10).reason, /at most 10/); assert.equal(canToggleSeat(ok, true, 10, 10).ok, true);
});
test('summaries group seats by stand, section and row', () => {
  const seats = [{stand: 'North', section: 'A', row: 'A1', number: 3}, {stand: 'North', section: 'A', row: 'A1', number: 1}, {stand: 'VIP', section: 'L', row: 'V1', number: 2}];
  assert.deepEqual(seatSummaryLines(seats), ['North · A · Row A1: seats 1, 3', 'VIP · L · Row V1: seat 2']);
  assert.equal(seatBookingText({booking_type: 'seat', quantity: 2, category: 'VIP'}), '2 seats (VIP)');
  assert.equal(seatBookingText({quantity: 3, category: 'General'}), '3 × General'); assert.deepEqual(seatSummaryLines(undefined), []);
  assert.ok(seatAria({number: 5, category: 'VIP', price: 1500, status: 'available'}, {label: 'A1'}, false).includes('Row A1, seat 5, VIP'));
});

/* Drive the real page with stubbed globals: backend data in, requests out. */
function harness(over = {}){
  const calls = [], els = {}, el = sel => (els[sel] = els[sel] || {innerHTML: '', textContent: '', dataset: {}}), toasts = [];
  const section = {match_id: 1, section: {id: 5, name: 'Block A', code: 'NBLOCKA'}, rows: [{id: 1, label: 'A1', seats: [
    {seat_id: 11, seat_key: 'K-A1-1', number: 1, status: 'available', selectable: true, price: 300, category: 'General'},
    {seat_id: 12, seat_key: 'K-A1-2', number: 2, status: 'booked', selectable: false, price: 300, category: 'General'},
    {seat_id: 13, seat_key: 'K-A1-3', number: 3, status: 'held', selectable: false, price: 300, category: 'General'},
    {seat_id: 14, seat_key: 'K-A1-4', number: 4, status: 'unavailable', selectable: false, price: 300, category: 'General'},
    {seat_id: 15, seat_key: 'K-A1-5', number: 5, status: 'available', selectable: true, price: 300, category: 'General'}]}]};
  const ctx = {console, crypto: {randomUUID: () => 'abcd-1234'}, sessionStorage: {setItem() {}}, location: {hash: '#/seats/1'}, state: over.state || {user: {id: 1}},
    _timers: [], setInterval: () => 1, $: el, esc: v => String(v), inr: n => '₹' + n, dt: s => s, toast: (m, bad) => toasts.push([m, !!bad]), handleGo() {}, loading() {}, show: h => { el('#view').shown = h; },
    emptyState: o => `EMPTY ${o.title}`, errorState: m => `ERR ${m}`,
    api: async (p, o = {}) => { calls.push([o.method || 'GET', p, o.body, o.headers]);
      if (over.api) { const r = await over.api(p, o); if (r !== undefined) return r; }
      if (p === '/matches/1') return {team_a: 'India', team_b: 'Australia', date: '2027-01-01', timing: '7pm', stadium: {name: 'Chepauk'}};
      if (p === '/matches/1/seating') return {seating_enabled: true, max_seats: 3, stands: [{id: 2, name: 'North Stand', available: 3, sections: [{id: 5, name: 'Block A', category: 'General', base_price: 300, available: 3}]}]};
      if (p === '/matches/1/seating/sections/5') return section;
      if (p === '/matches/1/seat-quote') return {quantity: o.body.seat_ids.length, subtotal: 300 * o.body.seat_ids.length, fee: 6, tax: 55, total: 361 * o.body.seat_ids.length};
      if (p === '/bookings/seats') return {id: 77, reference: 'BMM-1'};
      throw new Error('unexpected ' + p); }};
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'seatmap.js'), 'utf8').replace(/if \(typeof module[\s\S]*$/, '') + '\nthis.seatPage = seatPage;', ctx);
  const click = async (data, id) => { const b = {dataset: data, id: id || ''}; await el('#view').onclick({target: {closest: s => (s === 'button' ? b : null)}}); };
  return {ctx, calls, els, toasts, click, el};
}
const tick = () => new Promise(r => setImmediate(r));

test('page renders legend, stand and section tabs and real seat buttons from the backend', async () => {
  const h = harness(); await h.ctx.seatPage('1');
  const view = h.el('#view').shown, grid = h.els['#seatgrid'].innerHTML;
  assert.ok(view.includes('Select your seats') && view.includes('Already booked') && view.includes('Temporarily held'));
  assert.ok(h.els['#stands'].innerHTML.includes('North Stand') && h.els['#secs'].innerHTML.includes('Block A'));
  for (const c of ['seat available', 'seat booked', 'seat held', 'seat unavailable']) assert.ok(grid.includes(c), c);
  assert.equal((grid.match(/aria-disabled="true"/g) || []).length, 3);        // booked, held, unavailable
  assert.ok(grid.includes('aria-label="Row A1, seat 1, General, 300 rupees, available"'));
});
test('selecting seats asks the server for the price; unavailable seats and the max are refused', async () => {
  const h = harness(); await h.ctx.seatPage('1');
  await h.click({seat: '12', row: 'A1'});                                       // booked
  await h.click({seat: '13', row: 'A1'});                                       // held
  assert.deepEqual(h.toasts.map(t => t[1]), [true, true]); assert.ok(!h.calls.some(c => c[1] === '/matches/1/seat-quote'));
  await h.click({seat: '11', row: 'A1'}); await tick(); await h.click({seat: '15', row: 'A1'}); await tick();
  const quote = h.calls.filter(c => c[1] === '/matches/1/seat-quote').pop();
  assert.equal(JSON.stringify(quote[2]), JSON.stringify({seat_ids: [11, 15]}));                              // ids only: no prices are sent
  const sum = h.els['#sum'].innerHTML;
  assert.ok(sum.includes('2 seats') && sum.includes('₹722') && sum.includes('Block A · Row A1 · Seat 1') && sum.includes('2 of 3 seats selected') && !sum.includes('disabled>Reserve'));
  await h.click({remove: '11'}); await tick();
  assert.ok(h.els['#sum'].innerHTML.includes('1 of 3 seats selected'));
});
test('proceeding creates the hold with ids only and an Idempotency-Key, then goes to payment', async () => {
  const h = harness(); await h.ctx.seatPage('1');
  await h.click({seat: '11', row: 'A1'}); await tick(); await h.click({}, 'go'); await tick();
  const post = h.calls.find(c => c[1] === '/bookings/seats');
  assert.equal(JSON.stringify(post[2]), JSON.stringify({match_id: 1, seat_ids: [11]})); assert.ok(post[3]['Idempotency-Key']);
  assert.equal(h.ctx.location.hash, '#/pay/77');
});
test('a logged-out visitor is sent to log in instead of reserving', async () => {
  const h = harness({state: {user: null}}); await h.ctx.seatPage('1');
  await h.click({seat: '11', row: 'A1'}); await tick(); await h.click({}, 'go');
  assert.equal(h.ctx.location.hash, '#/login'); assert.ok(!h.calls.some(c => c[1] === '/bookings/seats'));
});
test('when the server says a seat was just taken the error is shown and nothing stays selected silently', async () => {
  const h = harness({api: async p => { if (p === '/bookings/seats') throw new Error('Seats not available: [11]. Someone else just took a seat; please choose again.'); }});
  await h.ctx.seatPage('1'); await h.click({seat: '11', row: 'A1'}); await tick(); await h.click({}, 'go'); await tick();
  assert.ok(h.toasts.some(t => t[1] && t[0].includes('Someone else just took')));
  assert.notEqual(h.ctx.location.hash, '#/pay/77');
});
test('a match without a seating plan shows an explanation, not a broken map', async () => {
  const h = harness({api: async p => (p === '/matches/1/seating' ? {seating_enabled: false} : undefined)}); await h.ctx.seatPage('1');
  assert.ok(h.el('#view').shown.includes('Seat selection is not available'));
});
