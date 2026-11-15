// Browser scripts share ONE global scope: declaring the same top-level const/function/class in two files is a SyntaxError that
// stops the whole app. This test keeps every file's top-level names unique (and every file syntactically valid).
'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const dir = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
const files = [...html.matchAll(/<script src="([^"]+\.js)"><\/script>/g)].map(m => m[1]).filter(f => !f.startsWith('http'));

test('index.html loads the expected scripts, in an order where helpers come before app.js', () => {
  for (const f of ['ui.js', 'tickets.js', 'seating.js', 'seatmap.js', 'app.js']) assert.ok(files.includes(f), f);
  assert.equal(files[files.length - 1], 'app.js');
});
test('every script is syntactically valid', () => { for (const f of files) assert.doesNotThrow(() => new vm.Script(fs.readFileSync(path.join(dir, f), 'utf8'), {filename: f}), f); });
test('no top-level name is declared in two scripts', () => {
  const seen = {};
  for (const f of files) for (const m of fs.readFileSync(path.join(dir, f), 'utf8').matchAll(/^(?:async\s+)?(?:function\*?\s+|const\s+|let\s+|class\s+)([A-Za-z_$][\w$]*)/gm)) (seen[m[1]] = seen[m[1]] || new Set()).add(f);
  const dups = Object.entries(seen).filter(([, v]) => v.size > 1).map(([k, v]) => `${k}: ${[...v].join(', ')}`);
  assert.deepEqual(dups, []);
});
