// Run: node --test frontend/tests/discover.test.js
'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const D = require('../discover.js');
const P = s => new URLSearchParams(s);

test('filters from the URL are sanitised; junk falls back to defaults', () => {
  const f = D.filtersFromParams(P('q=%20csk%20&sport_id=3&min_price=500&max_price=2000&sort=price_asc&date_from=2026-12-01&location=Chennai&tournament=IPL'));
  assert.deepEqual(f, {...D.DISCOVER_DEFAULTS, q: 'csk', sport_id: '3', min_price: '500', max_price: '2000', sort: 'price_asc', date_from: '2026-12-01', location: 'Chennai', tournament: 'IPL'});
  const bad = D.filtersFromParams(P('sport_id=abc&min_price=-5&max_price=NaN&sort=DROP TABLE&date_from=yesterday&date_to=2026-13-45'));
  assert.deepEqual(bad, D.DISCOVER_DEFAULTS);
  assert.equal(D.filtersFromParams(P('min_price=0')).min_price, '0');   // 0 is a valid price
  assert.equal(D.filtersFromParams(P('sort=popular')).sort, 'popular');
});
test('API query contains only set fields and paging', () => {
  assert.equal(D.queryFromFilters({...D.DISCOVER_DEFAULTS}, {limit: 12}), 'sort=date&limit=12');
  const q = P(D.queryFromFilters({...D.DISCOVER_DEFAULTS, q: 'a&b=c', max_price: '900', sort: 'price_desc'}, {limit: 12, offset: 24}));
  assert.equal(q.get('q'), 'a&b=c'); assert.equal(q.get('max_price'), '900'); assert.equal(q.get('sort'), 'price_desc'); assert.equal(q.get('offset'), '24'); assert.equal(q.get('min_price'), null);   // values are encoded, not injected
  assert.equal(P(D.queryFromFilters(D.DISCOVER_DEFAULTS, {limit: 5, offset: 0})).get('offset'), null);
});
test('shareable hash omits defaults and round-trips', () => {
  assert.equal(D.hashFromFilters({...D.DISCOVER_DEFAULTS}), '#/matches');
  const f = {...D.DISCOVER_DEFAULTS, q: 'kkr', sport_id: '1', sort: 'price_asc', min_price: '1000'};
  const h = D.hashFromFilters(f); assert.ok(h.startsWith('#/matches?'));
  assert.deepEqual(D.filtersFromParams(P(h.split('?')[1])), f);
});
test('active filters, price validation and chips', () => {
  assert.equal(D.hasActiveFilters(D.DISCOVER_DEFAULTS), false); assert.equal(D.hasActiveFilters({...D.DISCOVER_DEFAULTS, sort: 'price_asc'}), false); assert.equal(D.hasActiveFilters({...D.DISCOVER_DEFAULTS, teams: 'DC'}), true);
  assert.ok(D.priceProblem({...D.DISCOVER_DEFAULTS, min_price: '900', max_price: '100'})); assert.equal(D.priceProblem({...D.DISCOVER_DEFAULTS, min_price: '100', max_price: '900'}), ''); assert.equal(D.priceProblem({...D.DISCOVER_DEFAULTS, min_price: '100'}), '');
  const sports = [{id: 1, name: 'Cricket'}], chips = D.activeChips({...D.DISCOVER_DEFAULTS, q: 'csk', sport_id: '1', location: 'Goa', date_from: '2026-12-01', min_price: '500', max_price: '900', tournament: 'ISL', teams: 'DC,KKR'}, sports);
  assert.deepEqual(chips.map(c => c.clear.join('+')), ['q', 'sport_id', 'location', 'tournament', 'date_from+date_to', 'min_price+max_price', 'teams']);
  assert.ok(chips[1].label === 'Cricket' && chips[4].label.includes('2026-12-01 → any') && chips[5].label.includes('₹500') && chips[5].label.includes('₹900'));
  assert.deepEqual(D.activeChips(D.DISCOVER_DEFAULTS, sports), []);
  assert.equal(D.activeChips({...D.DISCOVER_DEFAULTS, max_price: '900'}, [])[0].label, 'up to ₹900');
});
