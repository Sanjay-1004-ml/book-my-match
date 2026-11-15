// Run: node --test frontend/tests/seating.test.js
'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {SV, SEAT_RULES, seatValidate, sampleLayout, specSeatCount, seatPct, seatStatusPill, seatEsc} = require('../seating.js');

test('price validation mirrors the server rules', () => {
  for (const ok of ['0', '300', '499.5', '1500.00', '1000000']) assert.equal(SV.price(ok), '', ok);
  for (const bad of ['', '-1', 'abc', '1.234', '1e3', '1000001', ' ']) assert.notEqual(SV.price(bad), '', bad);
  assert.equal(SV.priceOptional(''), ''); assert.notEqual(SV.priceOptional('x'), '');
});
test('row label, seat count, seat number and code rules', () => {
  assert.equal(SV.rowLabel('A1'), ''); assert.equal(SV.rowLabel(' a 1 '), ''); assert.notEqual(SV.rowLabel(''), ''); assert.notEqual(SV.rowLabel('ROW-1'), ''); assert.notEqual(SV.rowLabel('ABCDEFGHI'), '');
  assert.equal(SV.seatCount('20'), ''); assert.equal(SV.seatCount(500), ''); for (const bad of ['0', '501', '2.5', 'x', '']) assert.notEqual(SV.seatCount(bad), '', bad);
  assert.equal(SV.seatNumber('9999'), ''); assert.notEqual(SV.seatNumber('0'), ''); assert.notEqual(SV.seatNumber('10000'), '');
  assert.equal(SV.code(''), ''); assert.equal(SV.code('N2'), ''); assert.notEqual(SV.code('N-2'), ''); assert.notEqual(SV.code('ABCDEFGHIJKLM'), '');
  assert.equal(SV.color(''), ''); assert.equal(SV.color('#22C55E'), ''); assert.notEqual(SV.color('green'), '');
  assert.notEqual(SV.name('Stand name')('A'), ''); assert.equal(SV.name('Stand name')('  North   Stand '), '');
});
test('seatValidate returns only failing fields with the first message', () => {
  assert.deepEqual(seatValidate({label: 'A1', seat_count: '20'}, SEAT_RULES.row), {});
  const e = seatValidate({label: '', seat_count: '0'}, SEAT_RULES.row); assert.deepEqual(Object.keys(e).sort(), ['label', 'seat_count']);
  const c = seatValidate({name: 'VIP', base_price: '-5'}, SEAT_RULES.category); assert.deepEqual(Object.keys(c), ['base_price']);
  assert.ok(seatValidate({name: 'x'}, SEAT_RULES.layout).name);
});
test('sample layout is valid, has the example stands and categories, and its totals add up', () => {
  const s = sampleLayout();
  assert.deepEqual(s.stands.map(x => x.name), ['North Stand', 'South Stand', 'East Stand', 'West Stand', 'Premium Section', 'VIP Section']);
  assert.deepEqual(s.categories.map(c => [c.name, c.base_price]), [['General', 300], ['East Stand', 500], ['West Stand', 600], ['Premium', 1000], ['VIP', 1500]]);
  const cats = new Set(s.categories.map(c => c.name)), codes = new Set();
  for (const st of s.stands) for (const sec of st.sections) { assert.ok(cats.has(sec.category)); for (const r of sec.rows) { assert.equal(SV.rowLabel(r.label), ''); assert.equal(SV.seatCount(r.seat_count), ''); } codes.add(st.code + sec.name); }
  assert.equal(specSeatCount(s), 240);   // North 80, South 40, East 40, West 40, Premium 20, VIP 20
  assert.equal(specSeatCount({stands: []}), 0);
});
test('helpers: percentage, escaping, status pills', () => {
  assert.equal(seatPct(1, 4), 25); assert.equal(seatPct(0, 0), 0);
  assert.equal(seatEsc('<b onclick="x">&'), '&lt;b onclick=&quot;x&quot;&gt;&amp;');
  assert.ok(seatStatusPill('available').includes('ok') && seatStatusPill('blocked').includes('warn') && seatStatusPill('booked').includes('bad'));
  assert.ok(!seatStatusPill('<script>').includes('<script>'));
});

/* Render the real tab with stubbed globals (no browser): checks structure, accessibility hooks and HTML escaping. */
function render(apiMap){
  let html = ''; const els = {};
  const ctx = {console, document: {querySelectorAll: () => []}, location: {hash: ''}, prompt: () => null,
    $: sel => (els[sel] = els[sel] || {}), esc: seatEsc, inr: n => '₹' + n, dt: s => s, toast() {}, confirmBox: async () => true, handleGo() {},
    emptyState: ({title, text}) => `<div class="empty-state"><h3>${seatEsc(title)}</h3><p>${seatEsc(text)}</p></div>`, errorState: m => m, skeleton: {list: () => ''},
    show: h => { html = h; }, api: async path => { const k = Object.keys(apiMap).find(x => path.startsWith(x)); if (!k) throw new Error('unexpected ' + path); return apiMap[k]; }};
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'seating.js'), 'utf8').replace(/if \(typeof module[\s\S]*$/, '') + '\nthis.adminSeatingTab = adminSeatingTab;', ctx);
  return ctx.adminSeatingTab(new URLSearchParams('stadium_id=1'), '<h1>Admin</h1>').then(() => ({html, els}));
}
const stadium = {id: 1, name: 'Melbourne <b>Park</b>', capacity: 50000, sport: {id: 1, name: 'Cricket'}};
const tree = {id: 7, stadium_id: 1, name: 'Main "layout"', is_active: true, stadium_capacity: 50000, capacity_matches_stadium: false,
  totals: {stands: 1, sections: 1, active_sections: 1, rows: 1, seats: 20, active_seats: 20, categories: 1, matches_assigned: 0},
  categories: [{id: 3, name: 'General', base_price: 300, color: '#22c55e', seats: 20}],
  stands: [{id: 4, name: '<img src=x onerror=alert(1)>', code: 'N', is_active: true, seat_count: 20, sections: [{id: 5, name: 'Block A', code: 'NBLOCKA', category_id: 3, is_active: true, seat_count: 20, rows: [{id: 6, label: 'A1', category_id: null, seat_count: 20}]}]}]};

test('tab shows the creation form when a stadium has no layout', async () => {
  const {html, els} = await render({'/stadiums/1/matches': [], '/stadiums': [stadium], '/admin/seating/layouts': []});
  assert.ok(html.includes('No seating layout for Melbourne &lt;b&gt;Park&lt;/b&gt;') && html.includes('id="newLayout"') && html.includes('Create from sample (240 seats)'));
  assert.ok(html.includes('<label for="stsel">Stadium</label>') && html.includes('role="alert"') && !html.includes('<b>Park</b>'));
  assert.equal(typeof els['#newLayout'].onsubmit, 'function');
});
test('tab renders layout, prices, structure and matches; hostile names are escaped', async () => {
  const matches = [{id: 11, title: 'A vs B', date: '2027-01-01'}, {id: 12, title: 'C vs D', date: '2027-01-02'}];
  const {html} = await render({'/stadiums/1/matches': matches, '/stadiums': [stadium], '/admin/seating/layouts/7': tree, '/admin/seating/layouts': [{id: 7, stadium_id: 1, name: 'Main'}],
    '/admin/matches/11/seat-inventory/summary': {seating_enabled: true, total: 20, available: 19, held: 0, booked: 0, blocked: 1, by_section: []},
    '/admin/matches/12/seat-inventory/summary': {seating_enabled: false}});
  assert.ok(html.includes('Seat categories and prices') && html.includes('data-cat-save="3"') && html.includes('data-row-save="6"') && html.includes('data-add-row="5"'));
  assert.ok(html.includes('Total seats') && html.includes('Stadium capacity') && html.includes('data-assign="12"') && html.includes('data-unassign="11"'));
  assert.ok(html.includes('has 20 seats while the stadium capacity is 50,000'));
  assert.ok(!html.includes('<img src=x') && html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('data-stand-del=') && html.includes('data-sec-del=') && html.includes('data-row-del=') && html.includes('aria-label="Seat count for row A1"'));
});
