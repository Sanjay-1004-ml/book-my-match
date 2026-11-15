// Run: node --test frontend/tests/ui-art.test.js
'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const {teamInitials, teamBadge, stadiumArt, mediaBox, sportKey, hashStr} = require('../art.js');
const {skeleton, emptyState, errorState, V, validateFields, AUTH_RULES} = require('../ui.js');

test('team initials', () => {
  assert.equal(teamInitials('DC'), 'DC'); assert.equal(teamInitials('KKR'), 'KKR'); assert.equal(teamInitials('India'), 'IN');
  assert.equal(teamInitials('Chennai Super Kings'), 'CS'); assert.equal(teamInitials('  real  madrid '), 'RM'); assert.equal(teamInitials(''), '?'); assert.equal(teamInitials(null), '?');
});
test('team badge is deterministic, accessible and escapes hostile names', () => {
  assert.equal(teamBadge('Mumbai Indians'), teamBadge('Mumbai Indians'));
  assert.notEqual(hashStr('Mumbai Indians'), hashStr('Chennai Super Kings'));
  const b = teamBadge('<img src=x onerror=alert(1)>"');
  assert.ok(!b.includes('<img') && b.includes('&lt;img') && b.includes('role="img"') && b.includes('aria-label='));
  assert.ok(teamBadge('RCB', {size: 64}).includes('width="64"'));
});
test('stadium art exists for every sport and falls back for unknown sports', () => {
  for (const s of ['Cricket', 'Football', 'Basketball', 'Hockey', 'Badminton', 'Kabaddi', '', null]) { const a = stadiumArt(s); assert.ok(a.startsWith('<svg') && a.endsWith('</svg>') && a.includes('aria-hidden="true"'), s); }
  assert.equal(sportKey('Cricket'), 'cricket'); assert.equal(sportKey('Beach Football'), 'football'); assert.equal(sportKey('Kabaddi'), 'default');
  assert.notEqual(stadiumArt('Cricket'), stadiumArt('Hockey'));
});
test('mediaBox always draws the fallback art and only adds an img when a url exists (escaped, self-removing on error)', () => {
  const none = mediaBox(null, 'Cricket', 'Melbourne'); assert.ok(none.includes('<svg') && !none.includes('<img'));
  const withImg = mediaBox('https://x.test/a.jpg?a=1&b="2"', 'Cricket', 'M"x'); assert.ok(withImg.includes('<svg') && withImg.includes('<img') && withImg.includes('onerror="this.remove()"') && withImg.includes('&amp;b=&quot;2&quot;') && withImg.includes('alt="M&quot;x"'));
});
test('skeletons and states render accessible markup', () => {
  assert.equal((skeleton.cards(4).match(/class="card skel-card"/g) || []).length, 4);
  assert.ok(skeleton.page().includes('aria-busy="true"') && skeleton.detail().includes('aria-busy') && skeleton.list(3).split('skel-row').length === 4);
  const e = emptyState({title: '<b>No</b>', text: 'x', actionHtml: '<a href="#/">Go</a>'}); assert.ok(e.includes('&lt;b&gt;No') && e.includes('<a href="#/">Go</a>'));
  const er = errorState('Bad <script>'); assert.ok(er.includes('role="alert"') && er.includes('data-retry') && !er.includes('<script>')); assert.ok(!errorState('x', {retry: false}).includes('data-retry'));
});
test('validation mirrors the server rules', () => {
  assert.equal(V.email('a@b.co'), ''); assert.notEqual(V.email('a@b'), ''); assert.notEqual(V.email(''), '');
  assert.notEqual(V.password('short1'), ''); assert.notEqual(V.password('allletters'), ''); assert.notEqual(V.password('12345678'), ''); assert.equal(V.password('abcd1234'), '');
  assert.equal(V.phoneOptional(''), ''); assert.equal(V.phoneOptional('+91 98765-43210'), ''); assert.notEqual(V.phoneOptional('abc'), '');
  const ok = {name: 'Asha', email: 'a@x.com', phone: '', password: 'abcd1234', confirm_password: 'abcd1234'};
  assert.deepEqual(validateFields(ok, AUTH_RULES.register), {});
  assert.deepEqual(Object.keys(validateFields({name: 'A', email: 'bad', phone: 'x', password: 'short', confirm_password: 'nope'}, AUTH_RULES.register)).sort(), ['confirm_password', 'email', 'name', 'password', 'phone']);
  assert.deepEqual(Object.keys(validateFields({email: '', password: ''}, AUTH_RULES.login)).sort(), ['email', 'password']);
  assert.equal(validateFields({password: 'abcd1234', confirm_password: 'abcd1235', name: 'Asha', email: 'a@x.com'}, AUTH_RULES.register).confirm_password, 'Passwords do not match');
});
