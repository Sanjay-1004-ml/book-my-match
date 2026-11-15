'use strict';
/* Phase 3A: admin seating management (a "Seating" tab inside the existing Admin dashboard).
   The pure helpers at the top (validation, sample layout, totals) have no DOM access so they can be unit-tested in Node.
   The server remains the authority: every rule here is only early feedback. Loaded before app.js. */
const SEAT_LIMITS = {rowSeats: 500, price: 1000000};
const seatEsc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

const SV = {
  name: (label, lo = 2, hi = 60) => v => { const s = String(v ?? '').trim().replace(/\s+/g, ' '); return s.length >= lo && s.length <= hi ? '' : `${label} must be ${lo}-${hi} characters`; },
  price: v => {
    const s = String(v ?? '').trim();
    if (!/^\d+(\.\d{1,2})?$/.test(s)) return 'Enter a price such as 500 or 499.50 (no more than 2 decimals)';
    return Number(s) > SEAT_LIMITS.price ? `Price cannot exceed ${SEAT_LIMITS.price}` : '';
  },
  priceOptional: v => (String(v ?? '').trim() === '' ? '' : SV.price(v)),
  rowLabel: v => (/^[A-Za-z0-9]{1,8}$/.test(String(v ?? '').replace(/\s+/g, '')) ? '' : 'Row label: 1-8 letters or digits (for example A1)'),
  seatCount: v => { const n = Number(v); return Number.isInteger(n) && n >= 1 && n <= SEAT_LIMITS.rowSeats ? '' : `Seats per row must be a whole number from 1 to ${SEAT_LIMITS.rowSeats}`; },
  seatNumber: v => { const n = Number(v); return Number.isInteger(n) && n >= 1 && n <= 9999 ? '' : 'Seat number must be a whole number from 1 to 9999'; },
  code: v => (String(v ?? '').trim() === '' || /^[A-Za-z0-9]{1,12}$/.test(String(v).trim()) ? '' : 'Code: up to 12 letters or digits'),
  color: v => (!v || /^#[0-9a-fA-F]{6}$/.test(v) ? '' : 'Colour must look like #22c55e'),
  required: label => v => (v === undefined || v === null || String(v).trim() === '' ? `${label} is required` : ''),
};
/* values: {field: value}, rules: {field: [fn]} -> {field: firstMessage} (only failing fields) */
function seatValidate(values, rules){
  const out = {};
  for (const [f, checks] of Object.entries(rules)) for (const c of checks) { const m = c(values[f], values); if (m) { out[f] = m; break; } }
  return out;
}
const SEAT_RULES = {
  layout: {name: [SV.name('Layout name', 2, 80)]},
  category: {name: [SV.name('Category name', 1, 50)], base_price: [SV.price], color: [SV.color]},
  stand: {name: [SV.name('Stand name')], code: [SV.code]},
  section: {name: [SV.name('Section name')], category_id: [SV.required('Category')], code: [SV.code]},
  row: {label: [SV.rowLabel], seat_count: [SV.seatCount]},
  seat: {seat_number: [SV.seatNumber], price_override: [SV.priceOptional]},
};

/* A starter layout (same shape as the example in the brief) sized for demos; admins then edit it freely. */
function sampleLayout(){
  const rows = (prefix, n, seats) => Array.from({length: n}, (_, i) => ({label: `${prefix}${i + 1}`, seat_count: seats}));
  return {
    categories: [{name: 'General', base_price: 300}, {name: 'East Stand', base_price: 500}, {name: 'West Stand', base_price: 600},
                 {name: 'Premium', base_price: 1000}, {name: 'VIP', base_price: 1500}],
    stands: [
      {name: 'North Stand', code: 'N', sections: [{name: 'Block A', category: 'General', rows: rows('A', 2, 20)}, {name: 'Block B', category: 'General', rows: rows('B', 2, 20)}]},
      {name: 'South Stand', code: 'S', sections: [{name: 'Block A', category: 'General', rows: rows('A', 2, 20)}]},
      {name: 'East Stand', code: 'E', sections: [{name: 'Block A', category: 'East Stand', rows: rows('A', 2, 20)}]},
      {name: 'West Stand', code: 'W', sections: [{name: 'Block A', category: 'West Stand', rows: rows('A', 2, 20)}]},
      {name: 'Premium Section', code: 'P', sections: [{name: 'Premium Block', category: 'Premium', rows: rows('P', 2, 10)}]},
      {name: 'VIP Section', code: 'V', sections: [{name: 'VIP Lounge', category: 'VIP', rows: rows('V', 2, 10)}]},
    ],
  };
}
function specSeatCount(spec){ return (spec.stands || []).reduce((a, st) => a + st.sections.reduce((b, sec) => b + sec.rows.reduce((c, r) => c + r.seat_count, 0), 0), 0); }
const seatPct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0);
const STATUS_LABEL = {available: 'Available', held: 'Held', booked: 'Booked', blocked: 'Blocked'};
const seatStatusPill = s => `<span class="pill ${s === 'available' ? 'ok' : s === 'blocked' ? 'warn' : 'bad'}">${seatEsc(STATUS_LABEL[s] || s)}</span>`;

/* Renders {field: message} errors under the matching inputs (inputs need name="field" and a following .ferr element). */
function showFieldErrors(form, errors){
  form.querySelectorAll('[name]').forEach(el => {
    const msg = errors[el.name], slot = el.parentElement && el.parentElement.querySelector('.ferr');
    el.setAttribute('aria-invalid', msg ? 'true' : 'false'); if (slot) slot.textContent = msg || '';
  });
  const first = form.querySelector('[aria-invalid="true"]'); if (first) first.focus();
}
const fld = (name, label, attrs = '', hint = '') => `<div class="field"><label for="sf-${name}">${label}</label><input id="sf-${name}" name="${name}" ${attrs} aria-describedby="sf-${name}-e"><div class="ferr" id="sf-${name}-e" role="alert"></div>${hint ? `<div class="muted small">${hint}</div>` : ''}</div>`;

// ---------- the tab ----------
async function adminSeatingTab(p, head){
  const done = html => show(head + html);
  const act = (fn, msg) => async e => { try { await fn(e); toast(msg); adminSeatingTab(p, head); } catch (er) { toast(er.message, true); } };
  const stadiums = await api('/stadiums'); const layouts = await api('/admin/seating/layouts');
  const byStadium = Object.fromEntries(layouts.map(l => [l.stadium_id, l]));
  const sid = +(p.get('stadium_id') || stadiums[0]?.id); const cur = stadiums.find(s => s.id === sid) || stadiums[0];
  if (!cur) return done(emptyState({icon: '🏟️', title: 'No stadiums yet', text: 'Add a stadium first (Stadiums tab), then design its seating.'}));
  const lay = byStadium[cur.id];
  const picker = `<div class="filters"><div class="field"><label for="stsel">Stadium</label><select id="stsel">${stadiums.map(s => `<option value="${s.id}" ${s.id === cur.id ? 'selected' : ''}>${seatEsc(s.name)} (${seatEsc(s.sport.name)})${byStadium[s.id] ? ' · seating ✓' : ''}</option>`).join('')}</select></div></div>`;
  const wirePicker = () => { $('#stsel').onchange = e => { location.hash = '#/admin?tab=seating&stadium_id=' + e.target.value; }; };

  if (!lay){
    done(`${picker}${emptyState({icon: '💺', title: `No seating layout for ${cur.name}`, text: 'Create a layout, then add stands, sections, rows and seats. You can start from a sample.'})}
      <form id="newLayout" class="card stack-form" data-sf novalidate><h3>Create seating layout</h3>${fld('name', 'Layout name', `required maxlength="80" value="${seatEsc(cur.name)} seating"`)}
      <div class="row"><button class="btn" type="submit" name="mode" value="empty">Create empty layout</button><button class="btn ghost" type="button" id="sample">Create from sample (${specSeatCount(sampleLayout())} seats)</button></div>
      <p class="muted small">The sample has North, South, East, West, Premium and VIP stands with example prices. Everything can be edited afterwards. Stadium capacity is ${cur.capacity.toLocaleString('en-IN')}.</p></form>`);
    wirePicker();
    const create = (extra) => async () => {
      const f = $('#newLayout'), v = {name: f.elements['name'].value}; const errs = seatValidate(v, SEAT_RULES.layout); showFieldErrors(f, errs);
      if (Object.keys(errs).length) return;
      await act(() => api('/admin/seating/layouts', {method: 'POST', body: {stadium_id: cur.id, name: v.name.trim(), ...extra}}), 'Seating layout created')();
    };
    $('#newLayout').onsubmit = e => { e.preventDefault(); create({})(); };
    $('#sample').onclick = () => create(sampleLayout())();
    return;
  }

  const [tree, matches] = await Promise.all([api('/admin/seating/layouts/' + lay.id), api(`/stadiums/${cur.id}/matches`)]);
  const t = tree.totals, catName = Object.fromEntries(tree.categories.map(c => [c.id, c.name]));
  const catOpts = (sel) => tree.categories.map(c => `<option value="${c.id}" ${c.id === sel ? 'selected' : ''}>${seatEsc(c.name)} (${inr(c.base_price)})</option>`).join('');
  const mid = +(p.get('match_id') || 0); const selMatch = matches.find(m => m.id === mid);
  const summaries = await Promise.all(matches.slice(0, 30).map(m => api(`/admin/matches/${m.id}/seat-inventory/summary`).catch(() => ({seating_enabled: false}))));
  const sumOf = Object.fromEntries(matches.slice(0, 30).map((m, i) => [m.id, summaries[i]]));
  const stats = [['Stands', t.stands], ['Sections', `${t.active_sections}/${t.sections} active`], ['Rows', t.rows], ['Total seats', t.seats.toLocaleString('en-IN')], ['Active seats', t.active_seats.toLocaleString('en-IN')], ['Stadium capacity', tree.stadium_capacity.toLocaleString('en-IN')]];
  const struct = tree.stands.map(st => `<section class="card seat-stand" aria-label="${seatEsc(st.name)}">
      <div class="row between"><h3>${seatEsc(st.name)} <span class="muted small">code ${seatEsc(st.code)} · ${st.seat_count} seats</span> ${st.is_active ? '' : '<span class="pill warn">Inactive</span>'}</h3>
        <div class="row"><button class="btn sm ghost" data-stand-toggle="${st.id}" data-active="${st.is_active}">${st.is_active ? 'Deactivate' : 'Activate'}</button><button class="btn sm ghost" data-stand-rename="${st.id}" data-name="${seatEsc(st.name)}">Rename</button><button class="btn sm danger" data-stand-del="${st.id}" data-name="${seatEsc(st.name)}" data-seats="${st.seat_count}">Remove</button></div></div>
      ${st.sections.map(sec => `<div class="seat-section"><div class="row between"><b>${seatEsc(sec.name)}</b><span class="muted small">${seatEsc(sec.code)} · ${sec.seat_count} seats</span>
          <label class="inline">Category <select data-sec-cat="${sec.id}" aria-label="Category for ${seatEsc(sec.name)}">${catOpts(sec.category_id)}</select></label>
          <label class="inline"><input type="checkbox" data-sec-active="${sec.id}" ${sec.is_active ? 'checked' : ''}> On sale</label>
          <button class="btn sm danger" data-sec-del="${sec.id}" data-name="${seatEsc(sec.name)}" data-seats="${sec.seat_count}">Remove</button></div>
        <div class="tablewrap"><table><tr><th>Row</th><th>Seats</th><th>Category</th><th>Change seats</th><th></th></tr>${sec.rows.map(r => `<tr><td>${seatEsc(r.label)}</td><td>${r.seat_count}</td><td>${seatEsc(catName[r.category_id] || catName[sec.category_id] || '—')}</td>
          <td><input type="number" min="1" max="${SEAT_LIMITS.rowSeats}" value="${r.seat_count}" data-row-n="${r.id}" aria-label="Seat count for row ${seatEsc(r.label)}" style="width:90px"> <button class="btn sm" data-row-save="${r.id}">Save</button></td>
          <td><button class="btn sm ghost" data-row-seats="${r.id}" data-label="${seatEsc(r.label)}">View seats</button> <button class="btn sm danger" data-row-del="${r.id}" data-label="${seatEsc(r.label)}" data-seats="${r.seat_count}">Remove</button></td></tr>`).join('') || '<tr><td colspan="5" class="muted">No rows yet.</td></tr>'}</table></div>
        <details><summary>Add a row to ${seatEsc(sec.name)}</summary><form class="filters" data-sf data-add-row="${sec.id}" novalidate>${fld('label', 'Row label', 'maxlength="8" placeholder="A3"')}${fld('seat_count', 'Seats in row', 'type="number" min="1" value="20"')}${fld('start_number', 'First seat number', 'type="number" min="1" value="1"')}<button class="btn" type="submit">Add row</button></form></details></div>`).join('') || '<p class="muted">No sections yet.</p>'}
      <details><summary>Add a section to ${seatEsc(st.name)}</summary><form class="filters" data-sf data-add-sec="${st.id}" novalidate>${fld('name', 'Section name', 'maxlength="60" placeholder="Block C"')}<div class="field"><label for="sf-cat-${st.id}">Category</label><select name="category_id" id="sf-cat-${st.id}">${catOpts()}</select><div class="ferr" role="alert"></div></div><button class="btn" type="submit">Add section</button></form></details></section>`).join('');

  const matchRows = matches.map(m => { const s = sumOf[m.id] || {}; return `<tr><td>${seatEsc(m.title)}<div class="muted small">${dt(m.date)}</div></td>
      <td>${s.seating_enabled ? `${s.available} / ${s.total} available · ${s.blocked} blocked${s.held || s.booked ? ` · ${s.held} held · ${s.booked} booked` : ''}` : '<span class="muted">No seat selection</span>'}</td>
      <td>${s.seating_enabled ? `<button class="btn sm ghost" data-go="#/admin?tab=seating&stadium_id=${cur.id}&match_id=${m.id}">Inventory</button> <button class="btn sm danger" data-unassign="${m.id}" data-title="${seatEsc(m.title)}">Unassign</button>` : `<button class="btn sm" data-assign="${m.id}">Assign layout</button>`}</td></tr>`; }).join('');

  let inv = '';
  if (selMatch && sumOf[selMatch.id]?.seating_enabled){
    const s = sumOf[selMatch.id], stf = p.get('status') || '', secf = p.get('section_id') || '';
    const list = await api(`/admin/matches/${selMatch.id}/seat-inventory?limit=100${stf ? '&status=' + encodeURIComponent(stf) : ''}${secf ? '&section_id=' + encodeURIComponent(secf) : ''}`);
    inv = `<h3>Inventory: ${seatEsc(selMatch.title)}</h3><div class="grid">${[['Total', s.total], ['Available', s.available], ['Held', s.held], ['Booked', s.booked], ['Blocked', s.blocked]].map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div class="stat">${v}</div></div>`).join('')}</div>
      <div class="tablewrap"><table><tr><th>Section</th><th>Total</th><th>Available</th><th>Held</th><th>Booked</th><th>Blocked</th><th>Sold %</th></tr>${s.by_section.map(x => `<tr><td>${seatEsc(x.section)} ${x.is_active ? '' : '<span class="pill warn">Off sale</span>'}</td><td>${x.total}</td><td>${x.available}</td><td>${x.held}</td><td>${x.booked}</td><td>${x.blocked}</td><td>${seatPct(x.booked, x.total)}%</td></tr>`).join('')}</table></div>
      <form class="filters" id="invf"><div class="field"><label for="invs">Status</label><select id="invs"><option value="">All</option>${['available', 'held', 'booked', 'blocked'].map(x => `<option value="${x}" ${x === stf ? 'selected' : ''}>${STATUS_LABEL[x]}</option>`).join('')}</select></div>
        <div class="field"><label for="invsec">Section</label><select id="invsec"><option value="">All</option>${s.by_section.map(x => `<option value="${x.section_id}" ${String(x.section_id) === secf ? 'selected' : ''}>${seatEsc(x.section)}</option>`).join('')}</select></div></form>
      <div class="tablewrap"><table><tr><th><span class="sr">Select</span></th><th>Seat</th><th>Status</th><th>Category</th><th>Booking</th></tr>${list.items.map(i => `<tr><td><input type="checkbox" data-pick="${i.seat_id}" aria-label="Select seat ${seatEsc(i.seat_key)}" ${['available', 'blocked'].includes(i.status) ? '' : 'disabled'}></td><td>${seatEsc(i.seat_key)}</td><td>${seatStatusPill(i.status)}${i.is_active ? '' : ' <span class="pill warn">Off sale</span>'}</td><td>${seatEsc(catName[i.category_id] || '')}</td><td>${i.booking_reference ? `${seatEsc(i.booking_reference)}<div class="muted small">${seatEsc(i.customer || '')}${i.hold_expires_at ? ' · hold until ' + seatEsc(fdt(i.hold_expires_at)) : ''}</div>` : '<span class="muted">—</span>'}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">No seats match this filter.</td></tr>'}</table></div>
      <p class="muted small">Showing ${list.items.length} of ${list.total}. Held and booked seats cannot be changed here.</p>
      <div class="row"><button class="btn danger" id="blockSel">Block selected</button><button class="btn ghost" id="unblockSel">Unblock selected</button></div>`;
  }

  done(`${picker}
    <div class="row between"><h2>${seatEsc(tree.name)} ${tree.is_active ? '' : '<span class="pill warn">Inactive</span>'}</h2><button class="btn danger sm" id="delLayout">Delete layout</button></div>
    <div class="grid">${stats.map(([k, v]) => `<div class="card"><div class="muted">${k}</div><div class="stat">${v}</div></div>`).join('')}</div>
    ${tree.capacity_matches_stadium ? '' : `<p class="muted small">ℹ️ The layout has ${t.seats.toLocaleString('en-IN')} seats while the stadium capacity is ${tree.stadium_capacity.toLocaleString('en-IN')}. This is allowed.</p>`}
    <h3>Seat categories and prices</h3><div class="tablewrap"><table><tr><th>Category</th><th>Base price (₹)</th><th>Colour</th><th>Seats</th><th></th></tr>${tree.categories.map(c => `<tr><td><input value="${seatEsc(c.name)}" maxlength="50" data-cn="${c.id}" aria-label="Name of category ${seatEsc(c.name)}"></td><td><input type="number" min="0" step="0.01" value="${c.base_price}" data-cp="${c.id}" aria-label="Price of ${seatEsc(c.name)}" style="width:120px"></td><td><input type="color" value="${c.color || '#22c55e'}" data-cc="${c.id}" aria-label="Colour of ${seatEsc(c.name)}"></td><td>${c.seats}</td>
      <td><button class="btn sm" data-cat-save="${c.id}">Save</button> <button class="btn sm danger" data-cat-del="${c.id}" data-name="${seatEsc(c.name)}">Remove</button></td></tr>`).join('') || '<tr><td colspan="5" class="muted">No categories yet. Add one below.</td></tr>'}</table></div>
    <form class="filters" data-sf data-add-cat novalidate>${fld('name', 'New category', 'maxlength="50" placeholder="Premium"')}${fld('base_price', 'Base price (₹)', 'type="number" min="0" step="0.01" placeholder="1000"')}<button class="btn" type="submit">Add category</button></form>
    <p class="muted small">Price changes apply to future quotes only. The server always calculates the ticket amount; prices from the browser are never trusted.</p>
    <h3>Stands, sections and rows</h3>${struct || emptyState({icon: '🧱', title: 'No stands yet', text: 'Add the first stand below.'})}
    <form class="filters" data-sf data-add-stand novalidate>${fld('name', 'New stand', 'maxlength="60" placeholder="East Stand"')}${fld('code', 'Code (optional)', 'maxlength="12" placeholder="E"')}<button class="btn" type="submit">Add stand</button></form>
    <div id="rowSeats" aria-live="polite"></div>
    <h3>Matches at ${seatEsc(cur.name)}</h3><div class="tablewrap"><table><tr><th>Match</th><th>Seat inventory</th><th></th></tr>${matchRows || '<tr><td colspan="3" class="muted">No upcoming matches at this stadium.</td></tr>'}</table></div>
    <p class="muted small">Each match keeps its own seat inventory. Matches without a layout keep using category tickets exactly as before.</p>${inv}`);

  wirePicker();
  const num = (v, d) => (v === '' || v == null ? d : Number(v));
  const submitForm = (sel, rules, build, run, msg) => document.querySelectorAll(sel).forEach(f => { f.onsubmit = e => { e.preventDefault(); const v = Object.fromEntries(new FormData(f)); const errs = seatValidate(v, rules); showFieldErrors(f, errs); if (Object.keys(errs).length) return; act(() => run(f, build(v, f)), msg)(); }; });
  submitForm('[data-add-cat]', SEAT_RULES.category, v => ({name: v.name.trim(), base_price: v.base_price}), (f, b) => api(`/admin/seating/layouts/${lay.id}/categories`, {method: 'POST', body: b}), 'Category added');
  submitForm('[data-add-stand]', SEAT_RULES.stand, v => { const b = {name: v.name.trim()}; if (v.code.trim()) b.code = v.code.trim(); return b; }, (f, b) => api(`/admin/seating/layouts/${lay.id}/stands`, {method: 'POST', body: b}), 'Stand added');
  submitForm('[data-add-sec]', SEAT_RULES.section, v => { const b = {name: v.name.trim(), category_id: +v.category_id}; return b; }, (f, b) => api(`/admin/seating/stands/${f.dataset.addSec}/sections`, {method: 'POST', body: b}), 'Section added');
  submitForm('[data-add-row]', SEAT_RULES.row, v => ({label: v.label.replace(/\s+/g, ''), seat_count: +v.seat_count, start_number: num(v.start_number, 1)}), (f, b) => api(`/admin/seating/sections/${f.dataset.addRow}/rows`, {method: 'POST', body: b}), 'Row added');

  $('#view').onchange = e => { const t = e.target;
    if (t.dataset.secCat) act(() => api('/admin/seating/sections/' + t.dataset.secCat, {method: 'PUT', body: {category_id: +t.value}}), 'Section category updated')();
    if (t.dataset.secActive) act(async () => { if (!t.checked && !await confirmBox('Stop selling every seat in this section? Seats that are already booked stay valid.', {title: 'Take section off sale', yes: 'Take off sale', danger: true})) throw new Error('Not changed'); await api('/admin/seating/sections/' + t.dataset.secActive, {method: 'PUT', body: {is_active: t.checked}}); }, 'Section updated')();
    if (t.id === 'invs' || t.id === 'invsec') location.hash = `#/admin?tab=seating&stadium_id=${cur.id}&match_id=${mid}&status=${$('#invs').value}&section_id=${$('#invsec').value}`; };
  $('#view').onclick = async e => {
    const b = e.target.closest('button'); if (!b) return handleGo(e); const d = b.dataset;
    if (d.catSave) { const id = d.catSave; const v = {name: $(`[data-cn="${id}"]`).value, base_price: $(`[data-cp="${id}"]`).value}; const er = seatValidate(v, SEAT_RULES.category); if (er.name || er.base_price) return toast(er.name || er.base_price, true);
      return act(() => api('/admin/seating/categories/' + id, {method: 'PUT', body: {name: v.name.trim(), base_price: v.base_price, color: $(`[data-cc="${id}"]`).value}}), 'Category saved')(); }
    if (d.catDel) return act(async () => { if (!await confirmBox(`Remove the category "${d.name}"? It must not be in use.`, {title: 'Remove category', yes: 'Remove', danger: true})) throw new Error('Not removed'); await api('/admin/seating/categories/' + d.catDel, {method: 'DELETE'}); }, 'Category removed')();
    if (d.standToggle) return act(async () => { const on = d.active !== 'true'; if (!on && !await confirmBox('Stop selling every section in this stand?', {title: 'Take stand off sale', yes: 'Take off sale', danger: true})) throw new Error('Not changed'); await api('/admin/seating/stands/' + d.standToggle, {method: 'PUT', body: {is_active: on}}); }, 'Stand updated')();
    if (d.standRename) { const n = prompt('New stand name', d.name); if (n && n.trim()) return act(() => api('/admin/seating/stands/' + d.standRename, {method: 'PUT', body: {name: n.trim()}}), 'Stand renamed')(); return; }
    if (d.standDel) return act(async () => { if (!await confirmBox(`Remove the stand "${d.name}" with all its sections, rows and ${d.seats} seats? This cannot be undone.`, {title: 'Remove stand', yes: 'Remove stand', danger: true})) throw new Error('Not removed'); await api('/admin/seating/stands/' + d.standDel, {method: 'DELETE'}); }, 'Stand removed')();
    if (d.secDel) return act(async () => { if (!await confirmBox(`Remove the section "${d.name}" and its ${d.seats} seats? This cannot be undone.`, {title: 'Remove section', yes: 'Remove section', danger: true})) throw new Error('Not removed'); await api('/admin/seating/sections/' + d.secDel, {method: 'DELETE'}); }, 'Section removed')();
    if (d.rowDel) return act(async () => { if (!await confirmBox(`Remove row ${d.label} and its ${d.seats} seats? This cannot be undone.`, {title: 'Remove row', yes: 'Remove row', danger: true})) throw new Error('Not removed'); await api('/admin/seating/rows/' + d.rowDel, {method: 'DELETE'}); }, 'Row removed')();
    if (d.rowSave) { const n = +$(`[data-row-n="${d.rowSave}"]`).value; const er = SV.seatCount(n); if (er) return toast(er, true);
      return act(async () => { const cur = +$(`[data-row-n="${d.rowSave}"]`).defaultValue; if (n < cur && !await confirmBox(`Reduce this row from ${cur} to ${n} seats? The highest seat numbers are removed.`, {title: 'Remove seats', yes: 'Remove seats', danger: true})) throw new Error('Not changed'); await api('/admin/seating/rows/' + d.rowSave, {method: 'PUT', body: {seat_count: n}}); }, 'Row updated')(); }
    if (d.rowSeats) { $('#rowSeats').innerHTML = skeleton.list(2); try { const seats = await api(`/admin/seating/rows/${d.rowSeats}/seats`);
        $('#rowSeats').innerHTML = `<h3>Row ${seatEsc(d.label)} seats</h3><div class="tablewrap"><table><tr><th>Seat</th><th>Category</th><th>Price</th><th>Price override (₹)</th><th>On sale</th><th></th></tr>${seats.map(s => `<tr><td>${seatEsc(s.seat_key)}</td><td>${seatEsc(s.category)}</td><td>${inr(s.base_price)}</td><td><input type="number" min="0" step="0.01" value="${s.price_override ?? ''}" placeholder="—" data-so="${s.id}" aria-label="Price override for ${seatEsc(s.seat_key)}" style="width:110px"></td><td><input type="checkbox" data-sa="${s.id}" ${s.is_active ? 'checked' : ''} aria-label="On sale"></td><td><button class="btn sm" data-seat-save="${s.id}">Save</button> <button class="btn sm danger" data-seat-del="${s.id}" data-key="${seatEsc(s.seat_key)}">Remove</button></td></tr>`).join('')}</table></div>
          <form class="filters" data-sf id="addSeat" novalidate>${fld('seat_number', 'Add individual seat number', 'type="number" min="1" max="9999"')}${fld('price_override', 'Price override (optional)', 'type="number" min="0" step="0.01"')}<button class="btn" type="submit">Add seat</button></form>`;
        $('#addSeat').onsubmit = ev => { ev.preventDefault(); const f = ev.target, v = Object.fromEntries(new FormData(f)); const er = seatValidate(v, SEAT_RULES.seat); showFieldErrors(f, er); if (Object.keys(er).length) return;
          act(() => api(`/admin/seating/rows/${d.rowSeats}/seats`, {method: 'POST', body: {seat_number: +v.seat_number, ...(v.price_override !== '' ? {price_override: v.price_override} : {})}}), 'Seat added')(); };
      } catch (er) { $('#rowSeats').innerHTML = errorState(er.message, {retry: false}); } return; }
    if (d.seatSave) { const id = d.seatSave, po = $(`[data-so="${id}"]`).value; const er = SV.priceOptional(po); if (er) return toast(er, true);
      return act(() => api('/admin/seating/seats/' + id, {method: 'PUT', body: {price_override: po === '' ? null : po, is_active: $(`[data-sa="${id}"]`).checked}}), 'Seat saved')(); }
    if (d.seatDel) return act(async () => { if (!await confirmBox(`Remove seat ${d.key}? This cannot be undone.`, {title: 'Remove seat', yes: 'Remove seat', danger: true})) throw new Error('Not removed'); await api('/admin/seating/seats/' + d.seatDel, {method: 'DELETE'}); }, 'Seat removed')();
    if (d.assign) return act(() => api(`/admin/matches/${d.assign}/seating`, {method: 'PUT', body: {layout_id: lay.id}}), 'Layout assigned and seat inventory created')();
    if (d.unassign) return act(async () => { if (!await confirmBox(`Remove seat selection from "${d.title}"? Its seat inventory is deleted (not allowed while seats are held or booked).`, {title: 'Unassign layout', yes: 'Unassign', danger: true})) throw new Error('Not changed'); await api(`/admin/matches/${d.unassign}/seating`, {method: 'DELETE'}); }, 'Layout unassigned')();
    if (b.id === 'delLayout') return act(async () => { if (!await confirmBox(`Delete the whole layout "${tree.name}" with ${t.seats} seats? Matches must be unassigned first. This cannot be undone.`, {title: 'Delete seating layout', yes: 'Delete layout', danger: true})) throw new Error('Not deleted'); await api('/admin/seating/layouts/' + lay.id, {method: 'DELETE'}); }, 'Layout deleted')();
    if (b.id === 'blockSel' || b.id === 'unblockSel') { const ids = [...document.querySelectorAll('[data-pick]:checked')].map(x => +x.dataset.pick); if (!ids.length) return toast('Select at least one seat first', true);
      const block = b.id === 'blockSel'; return act(async () => { if (block && !await confirmBox(`Block ${ids.length} seat(s) for this match?`, {title: 'Block seats', yes: 'Block', danger: true})) throw new Error('Not changed'); const r = await api(`/admin/matches/${mid}/seat-inventory/status`, {method: 'PUT', body: {seat_ids: ids, status: block ? 'blocked' : 'available'}}); if (r.skipped) toast(`${r.skipped} seat(s) were skipped (held, booked or already in that state)`); }, block ? 'Seats blocked' : 'Seats unblocked')(); }
    handleGo(e);
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = {SV, SEAT_RULES, SEAT_LIMITS, seatValidate, sampleLayout, specSeatCount, seatPct, seatStatusPill, seatEsc, fld};
