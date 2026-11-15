// Run: node --test frontend/tests/food-cart.test.js      (Node 18+, no dependencies)
'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const {makeCart, FOOD_MAX_QTY, foodEmoji, foodMatches, cartLinesFor} = require('../food.js');
const store = () => { const m = {}; return {getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, raw: m}; };

test('add / increase / decrease / remove update quantities and the item count', () => {
  const c = makeCart(store());
  c.add(5); c.add(5); c.add(9, 3);
  assert.deepEqual(c.get(), {5: 2, 9: 3}); assert.equal(c.count(), 5);
  c.add(5, -1); assert.equal(c.qty(5), 1);
  c.add(5, -1); assert.equal(c.qty(5), 0); assert.deepEqual(c.get(), {9: 3});   // reaching 0 removes the line
  c.remove(9); assert.equal(c.count(), 0); assert.deepEqual(c.get(), {});
});
test('quantity is capped at the API limit and never negative', () => {
  const c = makeCart(store());
  c.setQty(1, 999); assert.equal(c.qty(1), FOOD_MAX_QTY);
  c.add(1, 5); assert.equal(c.qty(1), FOOD_MAX_QTY);
  c.setQty(2, -4); assert.equal(c.qty(2), 0); c.setQty(3, 'abc'); assert.equal(c.qty(3), 0);
});
test('cart persists in localStorage under the existing bmm_cart key and survives a reload', () => {
  const s = store(), a = makeCart(s); a.add(7, 2);
  assert.equal(s.raw.bmm_cart, '{"7":2}');
  assert.deepEqual(makeCart(s).get(), {7: 2});                                    // "reload": a new cart over the same storage
});
test('corrupt or hostile stored data is ignored, old-format carts still load', () => {
  const s = store(); s.setItem('bmm_cart', '{bad json'); assert.deepEqual(makeCart(s).get(), {});
  s.setItem('bmm_cart', JSON.stringify({1: 2, x: 3, 4: 0, 5: -1, 6: 99, 7: 1.9})); assert.deepEqual(makeCart(s).get(), {1: 2, 6: FOOD_MAX_QTY, 7: 1});
});
test('works (in memory) when storage is unavailable or throws', () => {
  const broken = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }};
  const c = makeCart(broken); c.add(1, 2); assert.equal(c.qty(1), 2);
  const n = makeCart(null); n.add(2); assert.equal(n.count(), 1);
});
test('onChange fires for every mutation (drives the nav badge)', () => {
  let calls = 0; const c = makeCart(store(), () => calls++);
  c.add(1); c.add(1, -1); c.clear(); assert.equal(calls, 3);
});
test('unavailable items are separated from priced lines (empty cart too)', () => {
  const menu = [{id: 1, name: 'Tea', price: 15}, {id: 2, name: 'Brownie', price: 80}];
  let r = cartLinesFor({1: 2, 2: 1, 9: 3}, menu);
  assert.deepEqual(r.lines.map(l => [l.name, l.qty]), [['Tea', 2], ['Brownie', 1]]); assert.deepEqual(r.missing, [{id: '9', qty: 3}]); assert.equal(r.estimate, 110);
  r = cartLinesFor({1: 2, 2: 1}, menu, [2]);                                       // the server flagged Brownie as unavailable
  assert.deepEqual(r.lines.map(l => l.name), ['Tea']); assert.equal(r.missing[0].id, '2'); assert.equal(r.estimate, 30);
  r = cartLinesFor({}, menu); assert.deepEqual([r.lines, r.missing, r.estimate], [[], [], 0]);
});
test('search and category filtering', () => {
  const i = {name: 'Vadapav', description: 'Spicy potato fritter', category: 'Chats', category_id: 2};
  assert.ok(foodMatches(i, '', 'all') && foodMatches(i, 'vada', 'all') && foodMatches(i, 'POTATO', '2') && foodMatches(i, ' chats ', 'all'));
  assert.ok(!foodMatches(i, 'pizza', 'all') && !foodMatches(i, '', '3'));
});
test('every seeded item gets an emoji tile when it has no image', () => {
  const names = ['Brownie','Pastry','Waffle','Donut','Ice Cream','Panipuri','Samosa','Vadapav','Pav Bhaji','Dahipuri','Water','Milkshake','Soft Drinks','Tea','Coffee','Fried Rice','Pizza','Popcorn','Burger','Sandwich','Parotta'];
  for (const n of names) assert.notEqual(foodEmoji({name: n, category: ''}), '🍴', n);
  assert.equal(foodEmoji({name: 'Mystery', category: 'Desserts'}), '🍰'); assert.equal(foodEmoji({name: 'Mystery', category: 'X'}), '🍴');
});
