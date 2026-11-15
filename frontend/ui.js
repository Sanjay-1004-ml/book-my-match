'use strict';
/* Small UI kit shared by every page: skeleton loaders, empty/error states and form validation rules.
   Pure string/logic functions (no DOM access) so they can be unit-tested in Node. Loaded before app.js. */
const uiEsc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

const skeleton = {
  card: () => '<div class="card skel-card" aria-hidden="true"><div class="skel skel-media"></div><div class="skel skel-line w60"></div><div class="skel skel-line w90"></div><div class="skel skel-line w40"></div></div>',
  cards: (n = 6) => `<div class="grid">${Array.from({length: n}, skeleton.card).join('')}</div>`,
  /* generic page placeholder: heading + a row of cards */
  page: (n = 6) => `<div role="status" aria-busy="true" aria-label="Loading"><div class="skel skel-title" aria-hidden="true"></div><div class="skel skel-line w40" aria-hidden="true" style="margin-bottom:18px"></div>${skeleton.cards(n)}</div>`,
  detail: () => '<div role="status" aria-busy="true" aria-label="Loading"><div class="skel skel-hero" aria-hidden="true"></div><div class="two" aria-hidden="true"><div><div class="skel skel-title"></div><div class="skel skel-line w90"></div><div class="skel skel-line w60"></div></div><div class="card"><div class="skel skel-line w60"></div><div class="skel skel-line w90"></div></div></div></div>',
  list: (n = 5) => `<div role="status" aria-busy="true" aria-label="Loading" aria-live="polite">${Array.from({length: n}, () => '<div class="skel skel-row" aria-hidden="true"></div>').join('')}</div>`,
};

function emptyState({icon = '🔍', title = 'Nothing here yet', text = '', actionHtml = ''} = {}){
  return `<div class="empty-state"><div class="es-icon" aria-hidden="true">${icon}</div><h3>${uiEsc(title)}</h3>${text ? `<p class="muted">${uiEsc(text)}</p>` : ''}${actionHtml ? `<div class="es-action">${actionHtml}</div>` : ''}</div>`;
}
function errorState(message, {retry = true} = {}){
  return `<div class="empty-state error-state" role="alert"><div class="es-icon" aria-hidden="true">⚠️</div><h3>Something went wrong</h3><p>${uiEsc(message)}</p>${retry ? '<div class="es-action"><button class="btn" data-retry>Try again</button> <a class="btn ghost" href="#/">Go home</a></div>' : ''}</div>`;
}

/* ---------- form validation (mirrors the server rules; the server remains the authority) ---------- */
const V = {
  required: label => v => (String(v ?? '').trim() ? '' : `${label} is required`),
  minLen: (label, n) => v => (String(v ?? '').trim().length >= n ? '' : `${label} must be at least ${n} characters`),
  email: v => (/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(v || '').trim()) ? '' : 'Enter a valid email address'),
  password: v => { v = String(v || ''); return v.length < 8 ? 'Password must be at least 8 characters' : !/[A-Za-z]/.test(v) || !/\d/.test(v) ? 'Password needs at least one letter and one digit' : ''; },
  phoneOptional: v => (!v || /^[+0-9 \-]{7,20}$/.test(String(v).trim()) ? '' : 'Enter a valid phone number (digits, +, - or spaces)'),
  matches: (other, label) => (v, all) => (v === all[other] ? '' : `${label} do not match`),
};
/* rules: {field: [fn(value, allValues) -> '' | message]}; returns {field: firstMessage} for failing fields only */
function validateFields(values, rules){
  const errors = {};
  for (const [field, checks] of Object.entries(rules)) for (const check of checks) { const m = check(values[field], values); if (m) { errors[field] = m; break; } }
  return errors;
}
const AUTH_RULES = {
  login: {email: [V.email], password: [V.required('Password')]},
  register: {name: [V.minLen('Name', 2)], email: [V.email], phone: [V.phoneOptional], password: [V.password], confirm_password: [V.matches('password', 'Passwords')]},
};

if (typeof module !== 'undefined' && module.exports) module.exports = {skeleton, emptyState, errorState, V, validateFields, AUTH_RULES, uiEsc};
