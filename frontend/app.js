'use strict';
const API = (window.BMM_API_BASE || 'http://127.0.0.1:8000/api');           // override by defining window.BMM_API_BASE before this script
const ICON = {Cricket:'🏏', Football:'⚽', Basketball:'🏀', Hockey:'🏑', Badminton:'🏸'};
const $ = (s, r=document) => r.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inr = n => '₹' + Number(n||0).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
const dt = s => new Date(s + 'T00:00:00').toLocaleDateString('en-IN', {weekday:'short', day:'numeric', month:'short', year:'numeric'});
const state = { user: null, token: localStorage.getItem('bmm_token') };
// the food cart (localStorage 'bmm_cart') lives in food.js, which is loaded before this file.

function toast(msg, err){
  if (msg === STALE) return;   // a cancelled navigation is not an error worth showing
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.setAttribute('role', err ? 'alert' : 'status');
  t.innerHTML = `<span class="t-ic" aria-hidden="true">${err ? '⚠️' : '✓'}</span><span class="t-msg"></span><button class="t-x" aria-label="Dismiss">×</button>`;
  t.querySelector('.t-msg').textContent = msg; t.querySelector('.t-x').onclick = () => t.remove();
  $('#toasts').append(t); setTimeout(() => t.remove(), err ? 6000 : 4000);
}
/* confirmBox('Delete it?') still works; options add a title, button label and a danger style for destructive actions */
function confirmBox(msg, {title = 'Please confirm', yes = 'Confirm', no = 'Cancel', danger = false} = {}){
  return new Promise(res => { const d = $('#dlg'); $('#dlgTitle').textContent = title; $('#dlgMsg').textContent = msg; $('#dlgYes').textContent = yes; $('#dlgNo').textContent = no;
    $('#dlgYes').className = 'btn' + (danger ? ' danger' : ''); d.onclose = () => res(d.returnValue === 'yes'); d.returnValue = 'no'; d.showModal(); });
}
function badge(){ const n = cart.count(), b = $('#cartCount'); b.hidden = !n; b.textContent = n; }
const loading = (kind = 'page') => { $('#view').innerHTML = (skeleton[kind] || skeleton.page)(); };
const show = html => { $('#view').innerHTML = html; window.scrollTo(0, 0); };

/* Each navigation cancels the previous page's in-flight GET requests, so a slow old page can never overwrite the new one.
   Only GETs are cancelled: a POST/PUT/DELETE (payment, booking, order) always runs to completion. */
const STALE = '__stale_navigation__'; let routeCtl = new AbortController();
async function api(path, {method='GET', body, headers={}}={}){
  const h = {...headers}; if (body) h['Content-Type']='application/json'; if (state.token) h.Authorization='Bearer '+state.token;
  let r; try { r = await fetch(API+path, {method, headers:h, body: body?JSON.stringify(body):undefined, signal: method==='GET' ? routeCtl.signal : undefined}); }
  catch (e) { throw new Error(e && e.name==='AbortError' ? STALE : 'Cannot reach the server. Is the backend running?'); }
  if (r.status===401 && state.token){ setAuth(null,null); }
  if (r.status===204) return null;
  const data = await r.json().catch(()=>({}));
  if (!r.ok){ let m = data.detail; if (Array.isArray(m)) m = m.map(e=>(e.loc||[]).slice(1).join('.')+': '+e.msg).join('; '); throw new Error(m || 'Request failed ('+r.status+')'); }
  return data;
}
function setAuth(token, user){ state.token=token; state.user=user; token?localStorage.setItem('bmm_token',token):localStorage.removeItem('bmm_token'); renderNav(); }
async function loadUser(){ if (!state.token) return renderNav(); try { state.user = await api('/auth/me'); } catch { state.user=null; } renderNav(); }
function renderNav(){
  if (typeof onAuthChange === 'function') onAuthChange();   // load/clear favorites when the signed-in user changes
  $('#adminLink').hidden = !(state.user && state.user.role === 'admin');
  $('#scanLink').hidden = !(state.user && ['staff', 'admin'].includes(state.user.role));
  $('#authLinks').innerHTML = state.user ? `<span class="chip-user" title="${esc(state.user.email)}"><span class="avatar" aria-hidden="true">${esc(teamInitials(state.user.name))}</span><span class="un">${esc(state.user.name.split(' ')[0])}</span></span> <a href="#/logout">Logout</a>`
    : '<a href="#/login">Login</a> <a class="btn sm" href="#/register">Register</a>';
}

// ---------- shared cards ----------
const sectionHead = (title, href, linkText = 'View all') => `<div class="sec-head"><h2>${title}</h2>${href ? `<a href="${href}">${linkText} →</a>` : ''}</div>`;
const pageHead = (title, sub = '', actions = '') => `<div class="page-head"><div><h1>${title}</h1>${sub ? `<p class="muted">${sub}</p>` : ''}</div>${actions ? `<div class="row">${actions}</div>` : ''}</div>`;
const matchCard = m => `<article class="card mcard click" data-go="#/match/${m.id}" tabindex="0" role="link" aria-label="${esc(m.team_a)} versus ${esc(m.team_b)}, ${dt(m.date)}">
  ${mediaBox(m.stadium.image_url, m.sport.name, m.stadium.name, 'mc-media')}
  <div class="mc-top"><span class="pill glass">${ICON[m.sport.name] || '🏟️'} ${esc(m.sport.name)}</span>${typeof favMatchBtn === 'function' ? favMatchBtn(m) : ''}</div>
  ${m.tournament ? `<div class="mc-tour">${esc(m.tournament)}</div>` : ''}
  <div class="mc-teams"><div class="mc-team">${teamBadge(m.team_a)}<b>${esc(m.team_a)}</b></div><span class="vs">VS</span><div class="mc-team">${teamBadge(m.team_b)}<b>${esc(m.team_b)}</b></div></div>
  <div class="mc-meta muted small"><span>📅 ${dt(m.date)} · ${esc(m.timing)}</span><span>📍 ${esc(m.stadium.name)}, ${esc(m.stadium.location)}</span></div>
  <div class="mc-foot"><div>${m.min_price != null ? `<span class="muted small">From</span> <span class="price">${inr(m.min_price)}</span>` : '<span class="muted small">Prices soon</span>'}</div>
    ${m.sold_out ? '<span class="pill bad">Sold out</span>' : `<span class="pill ok">${m.tickets_available.toLocaleString('en-IN')} left</span>`}</div></article>`;
const stadiumCard = s => { const prices = (s.categories || []).map(c => c.price);
  return `<article class="card mcard click" data-go="#/stadium/${s.id}" tabindex="0" role="link" aria-label="${esc(s.name)}, ${esc(s.location)}">
  ${mediaBox(s.image_url, s.sport.name, s.name, 'mc-media')}<h3>${esc(s.name)}</h3>
  <div class="muted small">📍 ${esc(s.location)} · 👥 ${s.capacity.toLocaleString('en-IN')} seats</div>
  <div class="mc-foot"><span class="pill">${ICON[s.sport.name] || '🏟️'} ${esc(s.sport.name)}</span>${prices.length ? `<span><span class="muted small">From</span> <span class="price">${inr(Math.min(...prices))}</span></span>` : ''}</div></article>`; };
const grid = (items, fn, empty) => items.length ? `<div class="grid">${items.map(fn).join('')}</div>` : `<div class="empty-state"><div class="es-icon" aria-hidden="true">🔍</div><p>${empty}</p></div>`;
const err = e => show(errorState(e.message));

// ---------- pages ----------
async function home(){
  loading();
  const [sports, up, pop, st] = await Promise.all([api('/sports'), api('/matches/upcoming?limit=6'), api('/matches/popular?limit=3'), api('/stadiums')]);
  show(`<section class="hero"><div class="hero-art" aria-hidden="true">${stadiumArt('cricket')}</div><div class="hero-body"><span class="eyebrow">Live sports · Instant e-tickets</span>
    <h1>Book your seat for the big match</h1><p>Cricket, football, basketball, hockey and badminton. Pick a match, grab your tickets, add snacks for match day.</p>
    <form id="hs" class="hero-search" role="search"><input name="q" placeholder="Search teams, stadiums, cities…" aria-label="Search matches"><button class="btn">Search matches</button></form>
    <div class="hero-chips">${sports.map(s => `<a class="chip" href="#/matches?sport_id=${s.id}">${ICON[s.name] || '🏟️'} ${esc(s.name)}</a>`).join('')}</div>
    <div class="hero-stats"><div><b>${sports.length}</b><span>Sports</span></div><div><b>${st.length}</b><span>Stadiums</span></div><div><b>QR</b><span>Instant e-tickets</span></div></div></div></section>
    ${sectionHead('Upcoming matches', '#/matches')}${grid(up, matchCard, 'No upcoming matches right now. Check back soon.')}
    ${sectionHead('Popular matches', '#/matches?sort=popular')}${grid(pop, matchCard, 'Popular matches will appear as tickets sell.')}
    ${sectionHead('Featured stadiums', '#/stadiums')}${grid(st.slice(0, 6), stadiumCard, 'No stadiums yet.')}
    <section class="promo"><div><h2>🍔 Hungry? Order food for your match</h2><p class="muted">Desserts, chats, beverages and main course. Order ahead and pick up at the stadium food counter.</p></div><a class="btn" href="#/food">Browse the menu</a></section>`);
  $('#hs').onsubmit = e => { e.preventDefault(); location.hash = '#/matches?q=' + encodeURIComponent(new FormData(e.target).get('q')); };
}
async function sportsPage(){
  loading(); const s = await api('/sports');
  show(`${pageHead('Sports', 'Choose a sport to see its stadiums and matches.')}${grid(s, x => `<article class="card mcard click" data-go="#/matches?sport_id=${x.id}" tabindex="0" role="link" aria-label="${esc(x.name)} matches">${mediaBox(null, x.name, x.name, 'mc-media')}
    <h3>${ICON[x.name] || '🏟️'} ${esc(x.name)}</h3><div class="muted small">${x.stadium_count} stadiums</div><div class="mc-foot"><a href="#/stadiums?sport_id=${x.id}">View stadiums</a><span class="pill ok">Matches →</span></div></article>`, 'No sports yet.')}`);
}

async function stadiumsPage(p){
  loading(); const q = p.get('sport_id') ? '?sport_id=' + encodeURIComponent(p.get('sport_id')) : '';
  show(`${pageHead('Stadiums', 'Venues, capacities and ticket prices.')}${grid(await api('/stadiums' + q), stadiumCard, 'No stadiums found.')}`);
}
async function stadiumPage(id){
  loading('detail'); const s = await api('/stadiums/' + id);
  show(`<a class="back" href="#/stadiums">← All stadiums</a><section class="detail-hero">${mediaBox(s.image_url, s.sport.name, s.name, 'dh-media')}<div class="dh-body"><span class="pill glass">${ICON[s.sport.name] || ''} ${esc(s.sport.name)}</span><h1>${esc(s.name)}</h1>
    <p>📍 ${esc(s.location)} · 👥 Capacity ${s.capacity.toLocaleString('en-IN')}</p></div></section>
    <h2>Ticket categories</h2><div class="row">${s.categories.map(c => `<span class="pill lg">${esc(c.name)} · ${inr(c.price)}</span>`).join('') || '<span class="muted">None configured.</span>'}</div>
    ${sectionHead('Upcoming matches')}${grid(s.upcoming_matches, matchCard, 'No upcoming matches at this stadium.')}`);
}

// ---------- match + booking ----------
async function matchPage(id){
  loading();
  const [m, cats, seating] = await Promise.all([api('/matches/'+id), api('/matches/'+id+'/ticket-categories'), api('/matches/'+id+'/seating').catch(() => ({seating_enabled: false}))]);
  const foodItems = await api('/food/items'); const c0 = cart.get(), cartLines = Object.entries(c0).filter(([i])=>foodItems.some(f=>f.id==i)), skippedFood = Object.keys(c0).length - cartLines.length;
  const S = { cat: null, qty: 1, food: false };
  show(`<a class="back" href="#/matches">← All matches</a><section class="detail-hero match-hero">${mediaBox(m.stadium.image_url, m.sport.name, m.stadium.name, 'dh-media')}
    <div class="dh-body"><div class="row between"><div class="row"><span class="pill glass">${ICON[m.sport.name] || '🏟️'} ${esc(m.sport.name)}</span>${m.tournament ? `<span class="pill glass">🏆 ${esc(m.tournament)}</span>` : ''}${m.status !== 'scheduled' ? `<span class="pill bad">${esc(m.status)}</span>` : ''}</div>${favMatchBtn(m)}</div>
      <div class="mh-badges">${teamBadge(m.team_a, {size: 60})}<span class="vs big">VS</span>${teamBadge(m.team_b, {size: 60})}</div>
      <h1>${esc(m.team_a)} vs ${esc(m.team_b)}</h1>
      <p>📅 ${dt(m.date)} · ${esc(m.timing)}<br>📍 <a href="#/stadium/${m.stadium.id}">${esc(m.stadium.name)}</a>, ${esc(m.stadium.location)}${m.min_price != null ? `<br>🎟️ Tickets from ${inr(m.min_price)}${m.max_price > m.min_price ? ' to ' + inr(m.max_price) : ''}` : ''}</p>
      <div class="row">${favTeamBtn(m.team_a)}${favTeamBtn(m.team_b)}</div></div></section>
    <div class="two"><div>
    <h2>Choose ticket category</h2><div id="cats">${cats.length?cats.map(c=>`<div class="opt ${c.available?'':'off'}" data-cat="${c.category_id}"><div><b>${esc(c.name)}</b><div class="muted small">${c.available.toLocaleString('en-IN')} of ${c.total.toLocaleString('en-IN')} available</div></div><span class="price">${inr(c.price)}</span></div>`).join(''):'<div class="empty">No tickets are on sale for this match.</div>'}</div>
    <p class="muted small">Tickets are category-based (no individual seat numbers).</p></div>
    <div class="card" id="sum"><h2>Booking summary</h2><div class="muted">Select a category to see the price.</div></div></div>`);
  if (seating.seating_enabled && m.status === 'scheduled'){   // Phase 3B: this match is sold by seat, not by category
    const free = seating.stands.reduce((a, st) => a + st.available, 0);
    $('#cats').parentElement.innerHTML = `<h2>Choose your seats</h2><p>This match has a seating plan. Pick your stand, section and exact seats.</p><p class="muted">${free.toLocaleString('en-IN')} seats currently available · up to ${seating.max_seats} per booking</p><a class="btn" href="#/seats/${m.id}">Select seats</a>`;
    $('#sum').remove(); return;
  }
  const sum = $('#sum');
  async function refresh(){
    if (!S.cat) return;
    const body = { match_id:+id, category_id:S.cat, quantity:S.qty, food: S.food ? cartLines.map(([i,q])=>({item_id:+i, quantity:q})) : [] };
    let q; try { q = await api('/bookings/quote', {method:'POST', body}); } catch(e){ sum.innerHTML = `<h2>Booking summary</h2><div class="error">${esc(e.message)}</div>`; return; }
    const t = q.tickets;
    sum.innerHTML = `<h2>Booking summary</h2><div class="qty"><button data-q="-1" aria-label="Fewer">−</button><b>${S.qty}</b><button data-q="1" aria-label="More">+</button> <span class="muted small">tickets</span></div>
      <table class="totals"><tr><td>${S.qty} × ${inr(t.unit_price)}</td><td align="right">${inr(t.subtotal)}</td></tr><tr><td>Booking fee (${q.rates.fee_percent}%)</td><td align="right">${inr(t.fee)}</td></tr><tr><td>Tax (${q.rates.ticket_tax_percent}%)</td><td align="right">${inr(t.tax)}</td></tr>
      ${q.food?`<tr class="sub"><td colspan="2"><b>🍴 Food</b></td></tr>${q.food_lines.map(l=>`<tr class="sub"><td>${esc(l.name)} × ${l.quantity}</td><td align="right">${inr(l.line_total)}</td></tr>`).join('')}${q.food.fee?`<tr class="sub"><td>Food service fee (${q.rates.food_fee_percent}%)</td><td align="right">${inr(q.food.fee)}</td></tr>`:''}<tr class="sub"><td>Food tax (${q.rates.food_tax_percent}%)</td><td align="right">${inr(q.food.tax)}</td></tr><tr><td>Food total</td><td align="right">${inr(q.food.total)}</td></tr>`:''}<tr class="grand"><td>Total</td><td align="right">${inr(q.grand_total)}</td></tr></table>
      ${cartLines.length?`<label class="row"><input type="checkbox" id="inclFood" ${S.food?'checked':''}> Pay my food cart together with these tickets (optional, ${cartLines.reduce((a,[,n])=>a+n,0)} items)</label> <a class="small" href="#/food">Edit cart</a><p class="small muted">Food is normally ordered and paid separately from the Food page. Tick this only if you want one combined payment.</p>`:'<p class="small muted">Want snacks? <a href="#/food">Add food to your cart</a> first, then come back.</p>'}
      ${skippedFood?`<p class="notice warn small">${skippedFood} cart item(s) are no longer available and will not be included.</p>`:''}
      <button class="btn" id="book" style="width:100%;margin-top:10px">Confirm booking</button><p class="notice">Demo mode: no real payment is taken. Prices are calculated by the server.</p>`;
  }
  $('#cats').onclick = e => { const o = e.target.closest('[data-cat]'); if (!o || o.classList.contains('off')) return; S.cat=+o.dataset.cat; document.querySelectorAll('.opt').forEach(x=>x.classList.toggle('sel', x===o)); refresh(); };
  const key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()+Math.random())).replace(/-/g,'');
  sum.onclick = async e => {
    const qb = e.target.closest('[data-q]'); if (qb){ S.qty = Math.max(1, Math.min(10, S.qty + +qb.dataset.q)); return refresh(); }
    if (e.target.id==='inclFood'){ S.food = e.target.checked; return refresh(); }
    if (e.target.id==='book'){
      if (!state.user){ toast('Please log in to book tickets', true); sessionStorage.setItem('bmm_next', location.hash); return location.hash='#/login'; }
      if (!await confirmBox('Confirm this booking?')) return;
      e.target.disabled = true;
      try {
        const b = await api('/bookings', {method:'POST', headers:{'Idempotency-Key':key}, body:{ match_id:+id, category_id:S.cat, quantity:S.qty, food: S.food ? cartLines.map(([i,q])=>({item_id:+i, quantity:q})) : [] }});
        if (S.food) cart.set({}); toast('Tickets reserved ('+b.reference+'). Complete payment to confirm.'); location.hash = '#/pay/'+b.id;
      } catch(er){ toast(er.message, true); e.target.disabled = false; refresh(); }
    }
  };
}

// ---------- my bookings ----------
const statusPill = b => `<span class="pill ${b.status==='confirmed'?'ok':b.status==='pending_payment'?'warn':'bad'}">${esc(b.status.replace('_',' '))}</span> <span class="pill warn">payment: ${esc(b.payment_status)} (demo)</span>`;
async function bookingsPage(){
  if (!need()) return; loading(); const list = await api('/bookings');
  show(`${pageHead('My Bookings', 'Reservations, payments and tickets.')}${list.length?`<div class="grid">${list.map(b=>`<div class="card click" data-go="#/booking/${b.id}"><div class="row between"><b>${esc(b.reference)}</b>${statusPill(b)}</div>
    <h3>${esc(b.match)}</h3><div class="muted small">${esc(b.sport)} · ${esc(b.stadium)}<br>${dt(b.match_date)} · ${esc(b.timing)}<br>${esc(seatBookingText(b))}${b.booking_type==='seat'?'<br>'+seatSummaryLines(b.seats).map(esc).join('<br>'):''}</div><div class="price">${inr(b.grand_total)}</div></div>`).join('')}</div>`:emptyState({icon:'🎟️', title:'No bookings yet', text:'Your reserved and confirmed tickets will show up here.', actionHtml:'<a class="btn" href="#/matches">Find a match</a>'})}`);
}
async function bookingPage(id){
  if (!need()) return; loading(); const b = await api('/bookings/'+id);
  show(`<a href="#/bookings">← My bookings</a><div class="card" style="margin-top:10px"><div class="row between"><h1>${esc(b.reference)}</h1><div>${statusPill(b)}</div></div>
    <h2>${esc(b.match)}</h2><p class="muted">${esc(b.sport)} · ${esc(b.stadium)}, ${esc(b.location)}<br>${dt(b.match_date)} · ${esc(b.timing)}<br>${b.booking_type==='seat'?esc(seatBookingText(b))+'<br>'+seatSummaryLines(b.seats).map(esc).join('<br>'):b.quantity+' × '+esc(b.category)+' (category-based tickets)'}</p>
    <table class="totals"><tr><td>Tickets</td><td align="right">${inr(b.subtotal)}</td></tr><tr><td>Fee</td><td align="right">${inr(b.fee)}</td></tr><tr><td>Tax</td><td align="right">${inr(b.tax)}</td></tr><tr><td>Food orders</td><td align="right">${inr(b.food_total)}</td></tr><tr class="grand"><td>Grand total</td><td align="right">${inr(b.grand_total)}</td></tr></table>
    ${b.status==='pending_payment'?`<p class="notice">Tickets are held until ${esc(fdt(b.expires_at))}. Pay before then or they are released.</p>`:''}
    ${b.refund_status==='demo_refund_pending'?'<p class="notice">Cancelled. Demo refund pending: no real refund was processed.</p>':''}
    ${b.food_orders.length?'<h3>Food orders</h3>'+b.food_orders.map(o=>`<div class="row between small" style="margin:4px 0"><span><a href="#/food/order/${o.id}"><b>${esc(o.order_number)}</b></a> (${esc(o.status)}${o.status==='placed'?', payment: '+esc(o.payment_status):''}): ${o.items.map(i=>esc(i.name)+' ×'+i.quantity).join(', ')} · ${inr(o.total)}</span>${b.status==='confirmed'&&o.status==='placed'&&o.payment_status!=='successful'?`<a class="btn sm" href="#/food/pay/${o.id}">Pay ${inr(o.total)}</a>`:''}</div>`).join('')+(b.status==='confirmed'?'<p class="small"><a href="#/food">Order more food for this match</a></p>':''):''}
    <div class="row" style="margin-top:14px">${b.status==='pending_payment'?`<a class="btn" href="#/pay/${b.id}">Complete payment</a>`:''}${b.status==='confirmed'?'<a class="btn" href="#/tickets">View my tickets</a>':''}
    <button class="btn ghost" id="pdf">Download confirmation (PDF)</button>${(b.status==='confirmed'||b.status==='pending_payment')?'<button class="btn danger" id="cancel">Cancel booking</button>':''}</div></div>`);
  const on = (sel, fn) => { const el=$(sel); if (el) el.onclick = fn; };
  on('#cancel', async()=>{ if(!await confirmBox('Cancel this booking? This cannot be undone.')) return; try{ await api(`/bookings/${id}/cancel`,{method:'POST'}); toast('Booking cancelled'); bookingPage(id);}catch(e){toast(e.message,true);} });
  on('#pdf', async()=>{ try{ const r = await fetch(`${API}/bookings/${id}/pdf`,{headers:{Authorization:'Bearer '+state.token}}); if(!r.ok) throw new Error('Could not download PDF'); const u=URL.createObjectURL(await r.blob()); const a=document.createElement('a'); a.href=u; a.download=b.reference+'.pdf'; a.click(); URL.revokeObjectURL(u);}catch(e){toast(e.message,true);} });
}

// ---------- food: see food.js ----------
// ---------- auth ----------
function authForm(kind){
  const reg = kind === 'register';
  const fld = (name, label, attrs = '', hint = '') => `<div class="field"><label for="f_${name}">${label}</label><input id="f_${name}" name="${name}" ${attrs} aria-describedby="e_${name}"><div class="ferr" id="e_${name}" role="alert"></div>${hint ? `<div class="muted small">${hint}</div>` : ''}</div>`;
  show(`<div class="auth-wrap"><div class="card auth-card"><h1>${reg ? 'Create your account' : 'Welcome back'}</h1><p class="muted">${reg ? 'Book tickets and order food in minutes.' : 'Log in to manage your bookings and tickets.'}</p>
    <form id="af" class="stack" novalidate>${reg ? fld('name', 'Name', 'autocomplete="name"') + fld('phone', 'Phone (optional)', 'type="tel" autocomplete="tel"') : ''}
    ${fld('email', 'Email', 'type="email" autocomplete="email"')}${fld('password', 'Password', `type="password" autocomplete="${reg ? 'new-password' : 'current-password'}"`, reg ? 'At least 8 characters with a letter and a digit.' : '')}
    ${reg ? fld('confirm_password', 'Confirm password', 'type="password" autocomplete="new-password"') : ''}
    <button class="btn block" id="afBtn">${reg ? 'Create account' : 'Login'}</button><p class="muted">${reg ? 'Have an account? <a href="#/login">Login</a>' : 'New here? <a href="#/register">Register</a>'}</p></form></div></div>`);
  const form = $('#af'), showErrors = errs => { form.querySelectorAll('input').forEach(i => { const m = errs[i.name] || ''; $('#e_' + i.name).textContent = m; i.setAttribute('aria-invalid', m ? 'true' : 'false'); }); };
  const values = () => Object.fromEntries(new FormData(form));
  form.addEventListener('blur', e => { if (e.target.name && e.target.value) { const errs = validateFields(values(), AUTH_RULES[kind]); const one = {}; if (errs[e.target.name]) one[e.target.name] = errs[e.target.name]; $('#e_' + e.target.name).textContent = one[e.target.name] || ''; e.target.setAttribute('aria-invalid', one[e.target.name] ? 'true' : 'false'); } }, true);
  form.onsubmit = async e => { e.preventDefault(); const body = values(); const errs = validateFields(body, AUTH_RULES[kind]); showErrors(errs);
    const bad = Object.keys(errs)[0]; if (bad) return form.elements[bad].focus();
    if (!body.phone) delete body.phone; const btn = $('#afBtn'); btn.disabled = true; btn.textContent = 'Please wait…';
    try { const r = await api('/auth/' + kind, {method: 'POST', body}); setAuth(r.token, r.user); toast('Welcome, ' + r.user.name); const nx = sessionStorage.getItem('bmm_next'); sessionStorage.removeItem('bmm_next'); location.hash = nx || '#/'; }
    catch (er) { toast(er.message, true); btn.disabled = false; btn.textContent = reg ? 'Create account' : 'Login'; } };
}
function need(){ if (state.user) return true; sessionStorage.setItem('bmm_next', location.hash); show(emptyState({icon: '🔒', title: 'Please log in to continue', text: 'You need an account to view this page.', actionHtml: '<a class="btn" href="#/login">Login</a> <a class="btn ghost" href="#/register">Register</a>'})); return false; }

// ---------- admin ----------
async function adminPage(p){
  if (!state.user || state.user.role!=='admin'){ return show('<div class="error">Admin access required.</div>'); }
  const tab = p.get('tab')||'dashboard';
  const tabs = ['dashboard','matches','stadiums','tickets','seating','entry','food','bookings','users'];
  const head = `<h1>Admin Dashboard</h1><div class="tabs">${tabs.map(t=>`<button data-go="#/admin?tab=${t}" class="${t===tab?'on':''}">${t==='tickets'?'Ticket pricing':t==='entry'?'Entry & tickets':t[0].toUpperCase()+t.slice(1)}</button>`).join('')}</div>`;
  loading();
  if (tab==='entry') return adminEntryTab(p, head);
  if (tab==='seating') return adminSeatingTab(p, head);   // Phase 3A (seating.js)
  const done = html => show(head + html);
  const act = (fn, msg) => async e => { try { await fn(e); toast(msg); adminPage(p); } catch(er){ toast(er.message, true); } };
  if (tab==='dashboard'){ const d = await api('/admin/dashboard'); return done(`<div class="grid">${[['Users',d.users],['Matches',d.matches],['Stadiums',d.stadiums],['Bookings',d.bookings],['Tickets booked',d.tickets_booked],['Food orders',d.food_orders],['Revenue (demo)',inr(d.revenue_demo)]].map(([k,v])=>`<div class="card"><div class="muted">${k}</div><div class="stat">${v}</div></div>`).join('')}</div><p class="muted small">${esc(d.revenue_note)}</p>`); }
  if (tab==='matches'){
    const [ms, st] = await Promise.all([api('/matches?include_past=true&status=&limit=100'), api('/stadiums')]);
    done(`<form id="nm" class="filters"><select name="stadium_id" required>${st.map(s=>`<option value="${s.id}">${esc(s.name)} (${esc(s.sport.name)})</option>`).join('')}</select><input name="team_a" placeholder="Team A" required><input name="team_b" placeholder="Team B" required><input type="date" name="match_date" required><input type="time" name="start_time" required><input name="tournament" maxlength="80" placeholder="Tournament (optional)"><button class="btn">Add match</button></form>
      <div class="tablewrap"><table><tr><th>Match</th><th>Stadium</th><th>Tournament</th><th>Date</th><th>Status</th><th></th></tr>${ms.map(m=>`<tr><td>${esc(m.title)}</td><td>${esc(m.stadium.name)}</td><td><input value="${esc(m.tournament||'')}" maxlength="80" data-mt="${m.id}" style="width:150px" placeholder="—"></td><td><input type="date" value="${m.date}" data-md="${m.id}"></td>
      <td><select data-ms="${m.id}">${['scheduled','cancelled','completed'].map(s=>`<option ${m.status===s?'selected':''}>${s}</option>`).join('')}</select></td><td><button class="btn danger sm" data-mdel="${m.id}">Delete</button></td></tr>`).join('')}</table></div><p class="muted small">Showing up to 100 matches. Cancelling a match cancels its bookings (demo refunds).</p>`);
    $('#nm').onsubmit = e => { e.preventDefault(); const b=Object.fromEntries(new FormData(e.target)); b.stadium_id=+b.stadium_id; b.start_time+=':00'; if(!b.tournament) delete b.tournament; act(()=>api('/admin/matches',{method:'POST',body:b}),'Match added')(); };
    $('#view').onchange = e => { const t=e.target; if (t.dataset.md) act(()=>api('/admin/matches/'+t.dataset.md,{method:'PUT',body:{match_date:t.value}}),'Date updated')(); if (t.dataset.mt) act(()=>api('/admin/matches/'+t.dataset.mt,{method:'PUT',body:{tournament:t.value.trim()||null}}),'Tournament updated')(); if (t.dataset.ms) act(async()=>{ if(t.value==='cancelled' && !await confirmBox('Cancel the match and all its bookings?', {title:'Cancel match', yes:'Cancel match', no:'Keep it', danger:true})) throw new Error('Not changed'); await api('/admin/matches/'+t.dataset.ms,{method:'PUT',body:{status:t.value}}); },'Status updated')(); };
    $('#view').onclick = e => { const b=e.target.closest('[data-mdel]'); if (b) act(async()=>{ if(!await confirmBox('Delete this match? This cannot be undone.', {title:'Delete match', yes:'Delete', danger:true})) throw new Error('Not deleted'); await api('/admin/matches/'+b.dataset.mdel,{method:'DELETE'}); },'Match deleted')(); handleGo(e); };
    return;
  }
  if (tab==='stadiums'){
    const [st, sp] = await Promise.all([api('/stadiums'), api('/sports')]);
    done(`<form id="ns" class="filters"><input name="name" placeholder="Stadium name" required><input name="location" placeholder="Location" required><input name="capacity" type="number" min="1" placeholder="Capacity" required><input name="image_url" placeholder="Image URL https://… (optional)"><select name="sport_id">${sp.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select><button class="btn">Add stadium</button></form>
      <div class="tablewrap"><table><tr><th>Stadium</th><th>Sport</th><th>Location</th><th>Capacity</th><th>Image URL</th><th></th></tr>${st.map(s=>`<tr><td>${esc(s.name)}</td><td>${esc(s.sport.name)}</td><td><input value="${esc(s.location)}" data-sl="${s.id}"></td><td><input type="number" min="1" value="${s.capacity}" data-sc="${s.id}" style="width:110px"></td><td><input value="${esc(s.image_url||'')}" data-si="${s.id}" style="width:180px" placeholder="https://…"></td><td><button class="btn sm" data-ss="${s.id}">Save</button></td></tr>`).join('')}</table></div>`);
    $('#ns').onsubmit = e => { e.preventDefault(); const b=Object.fromEntries(new FormData(e.target)); b.capacity=+b.capacity; b.sport_id=+b.sport_id; if(!b.image_url) delete b.image_url; act(()=>api('/admin/stadiums',{method:'POST',body:b}),'Stadium added')(); };
    $('#view').onclick = e => { const b=e.target.closest('[data-ss]'); if (b){ const id=b.dataset.ss; act(()=>api('/admin/stadiums/'+id,{method:'PUT',body:{location:$(`[data-sl="${id}"]`).value, capacity:+$(`[data-sc="${id}"]`).value, image_url:$(`[data-si="${id}"]`).value}}),'Stadium saved')(); } handleGo(e); };
    return;
  }
  if (tab==='tickets'){
    const st = await api('/stadiums'); const sid = +(p.get('stadium_id')||st[0]?.id); const cur = st.find(s=>s.id===sid)||st[0];
    const ms = cur ? await api(`/stadiums/${cur.id}/matches`) : [];
    done(`<div class="filters"><select id="stsel">${st.map(s=>`<option value="${s.id}" ${s.id===cur?.id?'selected':''}>${esc(s.name)}</option>`).join('')}</select></div>
      <h3>Categories & prices</h3><div class="tablewrap"><table>${(cur?.categories||[]).map(c=>`<tr><td>${esc(c.name)}</td><td><input type="number" min="0" step="0.01" value="${c.price}" data-cp="${c.id}" style="width:120px"></td><td><button class="btn sm" data-cs="${c.id}">Save price</button></td></tr>`).join('')}</table></div>
      <form id="nc" class="filters"><input name="name" placeholder="New category" required><input name="price" type="number" min="0" step="0.01" placeholder="Price ₹" required><input name="tickets_per_match" type="number" min="0" value="100" title="Tickets per upcoming match"><button class="btn">Add category</button></form>
      <h3>Inventory per match</h3><div class="tablewrap"><table>${ms.map(m=>`<tr><td colspan="3"><b>${esc(m.title)}</b> · ${dt(m.date)}</td></tr>`).join('')||'<tr><td>No upcoming matches.</td></tr>'}</table></div>
      <div class="filters"><select id="im">${ms.map(m=>`<option value="${m.id}">${esc(m.title)} · ${m.date}</option>`).join('')}</select><select id="ic">${(cur?.categories||[]).map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select><input id="it" type="number" min="0" placeholder="New total tickets"><button class="btn" id="isave">Set inventory</button></div>`);
    $('#stsel').onchange = e => location.hash = '#/admin?tab=tickets&stadium_id='+e.target.value;
    $('#nc').onsubmit = e => { e.preventDefault(); const b=Object.fromEntries(new FormData(e.target)); b.stadium_id=cur.id; b.price=+b.price; b.tickets_per_match=+b.tickets_per_match; act(()=>api('/admin/ticket-categories',{method:'POST',body:b}),'Category added')(); };
    $('#view').onclick = e => { const b=e.target.closest('[data-cs]'); if (b) act(()=>api('/admin/ticket-categories/'+b.dataset.cs,{method:'PUT',body:{price:+$(`[data-cp="${b.dataset.cs}"]`).value}}),'Price saved')();
      if (e.target.id==='isave') act(()=>api('/admin/inventory',{method:'PUT',body:{match_id:+$('#im').value,category_id:+$('#ic').value,total:+$('#it').value}}),'Inventory updated')(); handleGo(e); };
    return;
  }
  if (tab==='food'){
    const [items, cats] = await Promise.all([api('/admin/food/items'), api('/food/categories')]);
    done(`<form id="nf" class="filters"><select name="category_id">${cats.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select><input name="name" placeholder="Item name" required><input name="price" type="number" min="0" step="0.01" placeholder="Price ₹" required><input name="description" maxlength="300" placeholder="Description (optional)"><input name="image_url" placeholder="Image URL https://… (optional)"><button class="btn">Add item</button></form>
      <form id="nfc" class="filters"><input name="name" placeholder="New food category" required><button class="btn ghost">Add category</button></form>
      <div class="tablewrap"><table><tr><th>Item</th><th>Category</th><th>Price</th><th>Description</th><th>Image URL</th><th>Enabled</th><th></th></tr>${items.map(i=>`<tr><td>${esc(i.name)}</td><td>${esc(i.category)}</td><td><input type="number" min="0" step="0.01" value="${i.price}" data-fp="${i.id}" style="width:100px"></td><td><input value="${esc(i.description||'')}" maxlength="300" data-fd="${i.id}" style="width:200px"></td><td><input value="${esc(i.image_url||'')}" data-fi="${i.id}" style="width:160px" placeholder="https://…"></td><td><input type="checkbox" ${i.is_active?'checked':''} data-fa="${i.id}"></td><td><button class="btn sm" data-fs="${i.id}">Save</button></td></tr>`).join('')}</table></div>`);
    $('#nf').onsubmit = e => { e.preventDefault(); const b=Object.fromEntries(new FormData(e.target)); b.category_id=+b.category_id; b.price=+b.price; if(!b.description) delete b.description; if(!b.image_url) delete b.image_url; act(()=>api('/admin/food/items',{method:'POST',body:b}),'Item added')(); };
    $('#nfc').onsubmit = e => { e.preventDefault(); act(()=>api('/admin/food/categories',{method:'POST',body:Object.fromEntries(new FormData(e.target))}),'Category added')(); };
    $('#view').onclick = e => { const b=e.target.closest('[data-fs]'); if (b){ const id=b.dataset.fs; act(()=>api('/admin/food/items/'+id,{method:'PUT',body:{price:+$(`[data-fp="${id}"]`).value,is_active:$(`[data-fa="${id}"]`).checked,description:$(`[data-fd="${id}"]`).value,image_url:$(`[data-fi="${id}"]`).value}}),'Item saved')(); } handleGo(e); };
    return;
  }
  if (tab==='bookings'){
    const qv = p.get('q')||''; const list = await api('/admin/bookings?q='+encodeURIComponent(qv));
    done(`<form id="bs" class="filters"><input name="q" placeholder="Reference, name or email" value="${esc(qv)}"><button class="btn">Search</button></form><div class="tablewrap"><table><tr><th>Ref</th><th>User</th><th>Match</th><th>Qty</th><th>Total</th><th>Status</th><th>Refund</th></tr>${list.map(b=>`<tr><td>${esc(b.reference)}</td><td>${esc(b.user.email)}</td><td>${esc(b.match)}<div class="muted small">${dt(b.match_date)}</div></td><td>${b.quantity} × ${esc(b.category)}</td><td>${inr(b.grand_total)}</td><td>${esc(b.status)}</td><td>${esc(b.refund_status||'')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">No bookings.</td></tr>'}</table></div>`);
    $('#bs').onsubmit = e => { e.preventDefault(); location.hash='#/admin?tab=bookings&q='+encodeURIComponent(new FormData(e.target).get('q')); };
    return;
  }
  if (tab==='users'){
    const us = await api('/admin/users');
    done(`<div class="tablewrap"><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Active</th><th></th></tr>${us.map(u=>`<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td><select data-ur="${u.id}"><option ${u.role==='user'?'selected':''}>user</option><option ${u.role==='staff'?'selected':''}>staff</option><option ${u.role==='admin'?'selected':''}>admin</option></select></td><td><input type="checkbox" data-ua="${u.id}" ${u.is_active?'checked':''}></td><td><button class="btn sm" data-us="${u.id}">Save</button></td></tr>`).join('')}</table></div>`);
    $('#view').onclick = e => { const b=e.target.closest('[data-us]'); if (b){ const id=b.dataset.us; act(()=>api('/admin/users/'+id,{method:'PUT',body:{role:$(`[data-ur="${id}"]`).value,is_active:$(`[data-ua="${id}"]`).checked}}),'User saved')(); } handleGo(e); };
  }
}

// ---------- router ----------
function handleGo(e){ if (typeof handleFav === 'function' && handleFav(e)) return; const g = e.target.closest('[data-go]'); if (g) location.hash = g.dataset.go; }
async function route(){
  routeCtl.abort(); routeCtl = new AbortController();   // cancel the previous page's pending GETs
  if (window.BMM_cleanup) window.BMM_cleanup();   // stop timers / camera from the previous page
  $('#nav').classList.remove('open'); $('#menuBtn').setAttribute('aria-expanded', 'false'); $('#view').onclick = handleGo; $('#view').onchange = null;
  const [path, qs] = (location.hash.slice(1) || '/').split('?'); const p = new URLSearchParams(qs||''); const seg = path.split('/').filter(Boolean);
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#/'+(seg[0]||'')));
  try {
    if (!seg.length) await home();
    else if (seg[0]==='sports') await sportsPage();
    else if (seg[0]==='matches') await matchesPage(p);
    else if (seg[0]==='match') await matchPage(seg[1]);
    else if (seg[0]==='seats') await seatPage(seg[1]);   // Phase 3B (seatmap.js)
    else if (seg[0]==='stadiums') await stadiumsPage(p);
    else if (seg[0]==='stadium') await stadiumPage(seg[1]);
    else if (seg[0]==='bookings') await bookingsPage();
    else if (seg[0]==='booking') await bookingPage(seg[1]);
    else if (seg[0]==='food') await foodPage(seg);
    else if (seg[0]==='login') authForm('login');
    else if (seg[0]==='register') authForm('register');
    else if (seg[0]==='logout'){ try{ await api('/auth/logout',{method:'POST'}); }catch{} setAuth(null,null); toast('Logged out'); location.hash='#/'; }
    else if (seg[0]==='pay') await payPage(seg[1]);
    else if (seg[0]==='payment') await paymentResultPage(seg[1], seg[2], p);
    else if (seg[0]==='tickets') await ticketsPage(p);
    else if (seg[0]==='ticket') await ticketPage(seg[1]);
    else if (seg[0]==='scanner') await scannerPage();
    else if (seg[0]==='admin') await adminPage(p);
    else show(emptyState({icon: '🧭', title: 'Page not found', text: 'That page does not exist.', actionHtml: '<a class="btn" href="#/">Go home</a>'}));
  } catch(e){ if (e.message === STALE) return; err(e); }
  $('#view').focus({preventScroll: true});
}
$('#menuBtn').onclick = () => { const open = $('#nav').classList.toggle('open'); $('#menuBtn').setAttribute('aria-expanded', String(open)); };
// whole-card links: Enter/Space activate [data-go] cards; "Try again" on error states re-runs the current route
$('#view').addEventListener('keydown', e => { const g = e.target.closest && e.target.closest('[data-go]'); if (g && g === e.target && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); location.hash = g.dataset.go; } });
$('#view').addEventListener('click', e => { if (e.target.closest('[data-retry]')) route(); });
window.addEventListener('hashchange', route);
badge(); loadUser().then(route);
