'use strict';
/* Match discovery: search + filters + sort + paging, favorites (matches and teams).
   Loaded before app.js; like the other page files it only declares things and uses app.js helpers at call time.
   The server (/matches/discover, /matches/filters, /favorites) is the authority; this file builds requests and renders. */

// ---------- pure helpers (unit-tested in Node) ----------
const DISCOVER_DEFAULTS = {q: '', sport_id: '', location: '', tournament: '', date_from: '', date_to: '', min_price: '', max_price: '', sort: 'date', teams: ''};
const DISCOVER_SORTS = {date: 'Date: soonest first', date_desc: 'Date: latest first', price_asc: 'Price: low to high', price_desc: 'Price: high to low', popular: 'Most popular'};
const PAGE_SIZE = 12;
const isDate = s => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s));
const cleanPrice = v => { const n = Number(v); return v !== '' && v != null && Number.isFinite(n) && n >= 0 ? String(n) : ''; };

/* URLSearchParams (from the page hash) -> a sanitised filters object. Unknown/invalid values fall back to defaults. */
function filtersFromParams(p){
  const f = {...DISCOVER_DEFAULTS};
  for (const k of Object.keys(f)) { const v = p.get(k); if (v != null && v.trim() !== '') f[k] = v.trim(); }
  if (!(f.sort in DISCOVER_SORTS)) f.sort = 'date';
  if (f.sport_id && !/^\d+$/.test(f.sport_id)) f.sport_id = '';
  if (f.date_from && !isDate(f.date_from)) f.date_from = '';
  if (f.date_to && !isDate(f.date_to)) f.date_to = '';
  f.min_price = cleanPrice(f.min_price); f.max_price = cleanPrice(f.max_price);
  return f;
}
/* filters -> query string for the API (only set fields) */
function queryFromFilters(f, {limit, offset} = {}){
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== '' && v != null) q.set(k, v);
  if (limit != null) q.set('limit', limit);
  if (offset) q.set('offset', offset);
  return q.toString();
}
/* filters -> the shareable page hash (omits defaults) */
function hashFromFilters(f){
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== '' && v != null && v !== DISCOVER_DEFAULTS[k]) q.set(k, v);
  const s = q.toString(); return '#/matches' + (s ? '?' + s : '');
}
const hasActiveFilters = f => Object.keys(DISCOVER_DEFAULTS).some(k => k !== 'sort' && f[k] !== '');
function priceProblem(f){ return f.min_price !== '' && f.max_price !== '' && Number(f.min_price) > Number(f.max_price) ? 'Minimum price is higher than maximum price.' : ''; }
/* removable "active filter" chips: [{label, clear: [filterKeys]}] */
function activeChips(f, sports = []){
  const chips = [], inr0 = n => '₹' + Number(n).toLocaleString('en-IN');
  if (f.q) chips.push({label: `“${f.q}”`, clear: ['q']});
  if (f.sport_id) chips.push({label: (sports.find(s => String(s.id) === f.sport_id) || {name: 'Sport'}).name, clear: ['sport_id']});
  if (f.location) chips.push({label: '📍 ' + f.location, clear: ['location']});
  if (f.tournament) chips.push({label: '🏆 ' + f.tournament, clear: ['tournament']});
  if (f.date_from || f.date_to) chips.push({label: '📅 ' + (f.date_from || 'any') + ' → ' + (f.date_to || 'any'), clear: ['date_from', 'date_to']});
  if (f.min_price || f.max_price) chips.push({label: f.min_price && f.max_price ? `${inr0(f.min_price)} – ${inr0(f.max_price)}` : f.min_price ? `from ${inr0(f.min_price)}` : `up to ${inr0(f.max_price)}`, clear: ['min_price', 'max_price']});
  if (f.teams) chips.push({label: '♥ ' + f.teams.split(',').join(', '), clear: ['teams']});
  return chips;
}

// ---------- favorites client ----------
const favs = {uid: null, teams: new Set(), matches: new Set()};
function favsApply(s){ favs.teams = new Set(s.teams || []); favs.matches = new Set(s.match_ids || []); }
async function loadFavorites(){ try { favsApply(await api('/favorites')); refreshFavButtons(); } catch { /* favorites are optional: never block the page */ } }
function onAuthChange(){
  const uid = state.user ? state.user.id : null; if (uid === favs.uid) return;
  favs.uid = uid; favs.teams = new Set(); favs.matches = new Set(); refreshFavButtons(); if (uid) loadFavorites();
}
const favLabel = (on, name) => `${on ? 'Remove from favorites' : 'Add to favorites'}: ${name}`;
const favMatchBtn = m => { const on = favs.matches.has(m.id), name = `${m.team_a} vs ${m.team_b}`;
  return `<button class="fav ${on ? 'on' : ''}" data-favm="${m.id}" data-name="${esc(name)}" aria-pressed="${on}" aria-label="${esc(favLabel(on, name))}" title="${on ? 'Remove from favorites' : 'Add to favorites'}">${on ? '♥' : '♡'}</button>`; };
const favTeamBtn = name => { const on = favs.teams.has(name);
  return `<button class="btn ghost sm fav-team ${on ? 'on' : ''}" data-favt="${esc(name)}" aria-pressed="${on}">${on ? '♥ Following' : '♡ Follow'} ${esc(name)}</button>`; };
function refreshFavButtons(){
  document.querySelectorAll('[data-favm]').forEach(b => { const on = favs.matches.has(+b.dataset.favm); b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); b.setAttribute('aria-label', favLabel(on, b.dataset.name || '')); b.textContent = on ? '♥' : '♡'; b.title = on ? 'Remove from favorites' : 'Add to favorites'; });
  document.querySelectorAll('[data-favt]').forEach(b => { const on = favs.teams.has(b.dataset.favt), n = b.dataset.favt; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); b.textContent = (on ? '♥ Following ' : '♡ Follow ') + n; });
}
/* click handler used by handleGo(): returns true when the click was a favorite toggle (so the card is NOT opened) */
function handleFav(e){
  const b = e.target.closest('[data-favm],[data-favt]'); if (!b) return false;
  e.preventDefault(); e.stopPropagation();
  if (!state.user) { toast('Log in to save favorites', true); sessionStorage.setItem('bmm_next', location.hash); location.hash = '#/login'; return true; }
  const isMatch = b.dataset.favm != null, key = isMatch ? +b.dataset.favm : b.dataset.favt, on = isMatch ? favs.matches.has(key) : favs.teams.has(key);
  const path = isMatch ? '/favorites/matches/' + key : '/favorites/teams?name=' + encodeURIComponent(key);
  b.disabled = true;
  api(path, {method: on ? 'DELETE' : 'PUT'}).then(s => { favsApply(s); refreshFavButtons(); toast(on ? 'Removed from favorites' : (isMatch ? 'Added to favorites' : `Following ${key}`));
      if (window.BMM_favChanged) window.BMM_favChanged(); })
    .catch(er => toast(er.message, true)).finally(() => { b.disabled = false; });
  return true;
}

// ---------- the matches page ----------
async function matchesPage(p){
  loading();
  const facets = await api('/matches/filters');
  let F = filtersFromParams(p), tab = p.get('tab') === 'favorites' ? 'favorites' : 'all';
  const S = {items: [], total: 0, offset: 0, seq: 0, busy: false, error: '', timer: null};
  const opts = (list, cur, any) => `<option value="">${any}</option>${list.map(o => `<option value="${esc(o.v)}" ${String(cur) === String(o.v) ? 'selected' : ''}>${esc(o.t)}</option>`).join('')}`;
  const tabs = `<div class="tabs" role="tablist"><button role="tab" aria-selected="${tab === 'all'}" class="${tab === 'all' ? 'on' : ''}" data-tab="all">All matches</button><button role="tab" aria-selected="${tab === 'favorites'}" class="${tab === 'favorites' ? 'on' : ''}" data-tab="favorites">♥ My favorites</button></div>`;
  show(`${pageHead('Matches', 'Search, filter and sort to find the right match.')}${tabs}<div id="tabBody"></div>`);

  // ===== favorites tab =====
  async function favoritesTab(){
    const body = $('#tabBody');
    if (!state.user) { sessionStorage.setItem('bmm_next', '#/matches?tab=favorites'); body.innerHTML = emptyState({icon: '♡', title: 'Log in to see your favorites', text: 'Save matches and follow teams to find them quickly.', actionHtml: '<a class="btn" href="#/login">Login</a> <a class="btn ghost" href="#/register">Register</a>'}); return; }
    body.innerHTML = skeleton.cards(3);
    let s; try { s = await api('/favorites'); } catch (e) { body.innerHTML = errorState(e.message); return; }
    favsApply(s);
    let teamMatches = [];
    if (s.teams.length) { try { teamMatches = (await api('/matches/discover?limit=24&teams=' + encodeURIComponent(s.teams.join(',')))).items; } catch { /* shown empty */ } }
    const upcomingFav = s.matches.filter(m => m.status === 'scheduled');
    body.innerHTML = `${sectionHead('Followed teams')}${s.teams.length ? `<div class="row">${s.teams.map(favTeamBtn).join('')}</div>` : '<p class="muted">You are not following any team yet. Open a match and tap “Follow”.</p>'}
      ${sectionHead('Favorite matches')}${grid(s.matches, matchCard, 'No favorite matches yet. Tap the ♡ on any match card.')}
      ${s.teams.length ? sectionHead('Upcoming matches of your teams') + grid(teamMatches, matchCard, 'No upcoming matches for your teams right now.') : ''}`;
    window.BMM_favChanged = () => { if (tab === 'favorites') favoritesTab(); };
  }

  // ===== all-matches tab =====
  function allTab(){
    $('#tabBody').innerHTML = `<form id="ff" class="fpanel card" role="search" aria-label="Match filters" novalidate>
      <div class="frow1"><input id="f_q" name="q" type="search" placeholder="Search teams, stadiums…" value="${esc(F.q)}" aria-label="Search" autocomplete="off">
        <select name="sort" aria-label="Sort by">${Object.entries(DISCOVER_SORTS).map(([k, t]) => `<option value="${k}" ${F.sort === k ? 'selected' : ''}>${t}</option>`).join('')}</select>
        <button type="button" class="btn ghost sm" id="ftoggle" aria-expanded="false" aria-controls="fmore">Filters</button></div>
      <div id="fmore" class="fmore"><label>Sport<select name="sport_id">${opts(facets.sports.map(s => ({v: s.id, t: s.name})), F.sport_id, 'All sports')}</select></label>
        <label>City<select name="location">${opts(facets.cities.map(c => ({v: c, t: c})), F.location, 'All cities')}</select></label>
        ${facets.tournaments.length ? `<label>Tournament<select name="tournament">${opts(facets.tournaments.map(t => ({v: t, t})), F.tournament, 'All tournaments')}</select></label>` : ''}
        <label>From date<input type="date" name="date_from" value="${esc(F.date_from)}"></label><label>To date<input type="date" name="date_to" value="${esc(F.date_to)}"></label>
        <label>Min price (₹)<input type="number" name="min_price" min="0" step="50" inputmode="numeric" placeholder="${facets.price.min ?? ''}" value="${esc(F.min_price)}"></label>
        <label>Max price (₹)<input type="number" name="max_price" min="0" step="50" inputmode="numeric" placeholder="${facets.price.max ?? ''}" value="${esc(F.max_price)}"></label></div>
      <div class="ferr" id="fprice" role="alert"></div></form>
      <div id="chips" class="row chips-row"></div><div id="count" class="muted small" aria-live="polite"></div><div id="results"></div><div class="row center" id="more"></div>`;
    const form = $('#ff');
    const readForm = () => { const v = Object.fromEntries(new FormData(form)); F = filtersFromParams(new URLSearchParams({...v, teams: F.teams})); };
    const sync = () => history.replaceState(null, '', hashFromFilters(F));   // keeps the URL shareable without re-rendering the page
    const fetchPage = async reset => {
      const prob = priceProblem(F); $('#fprice').textContent = prob; if (prob) return;
      if (reset) { S.offset = 0; S.items = []; }
      const my = ++S.seq; S.busy = true; S.error = '';
      if (reset) $('#results').innerHTML = skeleton.cards(6);
      $('#more').innerHTML = reset ? '' : '<button class="btn ghost" disabled>Loading…</button>';
      try { const r = await api('/matches/discover?' + queryFromFilters(F, {limit: PAGE_SIZE, offset: S.offset})); if (my !== S.seq) return; S.items = S.items.concat(r.items); S.total = r.total; S.offset = S.items.length; S.has_more = r.has_more; }
      catch (e) { if (my !== S.seq) return; if (e.message === STALE) return; S.error = e.message; }
      S.busy = false; paint();
    };
    const paint = () => {
      $('#chips').innerHTML = activeChips(F, facets.sports).map((c, i) => `<button class="chip on" data-clear="${c.clear.join(',')}" aria-label="Remove filter ${esc(c.label)}">${esc(c.label)} ✕</button>`).join('') + (hasActiveFilters(F) ? '<button class="btn ghost sm" id="reset">Reset all</button>' : '');
      if (S.error) { $('#count').textContent = ''; $('#results').innerHTML = errorState(S.error); $('#more').innerHTML = ''; return; }
      $('#count').textContent = S.total ? `Showing ${S.items.length} of ${S.total} match${S.total === 1 ? '' : 'es'}` : '';
      $('#results').innerHTML = S.items.length ? `<div class="grid">${S.items.map(matchCard).join('')}</div>`
        : emptyState({icon: '🔍', title: 'No matches found', text: hasActiveFilters(F) ? 'Try removing a filter or widening your price range or dates.' : 'There are no upcoming matches right now.', actionHtml: hasActiveFilters(F) ? '<button class="btn" id="reset2">Clear all filters</button>' : ''});
      $('#more').innerHTML = S.has_more ? '<button class="btn ghost" id="loadMore">Load more matches</button>' : '';
    };
    const apply = () => { readForm(); sync(); fetchPage(true); };
    form.addEventListener('submit', e => { e.preventDefault(); clearTimeout(S.timer); apply(); });
    form.addEventListener('input', e => { if (e.target.id === 'f_q' || e.target.type === 'number') { clearTimeout(S.timer); S.timer = setTimeout(apply, 350); } });
    form.addEventListener('change', e => { if (e.target.id !== 'f_q' && e.target.type !== 'number') { clearTimeout(S.timer); apply(); } });
    $('#ftoggle').onclick = () => { const o = $('#fmore').classList.toggle('open'); $('#ftoggle').setAttribute('aria-expanded', String(o)); };
    if (F.sport_id || F.location || F.tournament || F.date_from || F.date_to || F.min_price || F.max_price) { $('#fmore').classList.add('open'); $('#ftoggle').setAttribute('aria-expanded', 'true'); }
    const clearKeys = keys => { keys.forEach(k => { F[k] = DISCOVER_DEFAULTS[k]; if (form.elements[k]) form.elements[k].value = ''; }); sync(); fetchPage(true); };
    $('#tabBody').onclick = e => {
      const c = e.target.closest('[data-clear]'); if (c) return clearKeys(c.dataset.clear.split(','));
      if (e.target.id === 'reset' || e.target.id === 'reset2') { F = {...DISCOVER_DEFAULTS, sort: F.sort}; form.reset(); [...form.elements].forEach(x => { if (x.name && x.name !== 'sort') x.value = ''; }); sync(); return fetchPage(true); }
      if (e.target.id === 'loadMore') return fetchPage(false);
    };
    paint(); fetchPage(true);
  }

  function renderTab(){ window.BMM_favChanged = null; tab === 'favorites' ? favoritesTab() : allTab(); }
  $('#view').onclick = e => { const t = e.target.closest('[data-tab]');
    if (t) { tab = t.dataset.tab; document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('on', b === t); b.setAttribute('aria-selected', b === t); }); history.replaceState(null, '', tab === 'favorites' ? '#/matches?tab=favorites' : hashFromFilters(F)); return renderTab(); }
    handleGo(e); };
  renderTab();
}

if (typeof module !== 'undefined' && module.exports) module.exports = {DISCOVER_DEFAULTS, DISCOVER_SORTS, filtersFromParams, queryFromFilters, hashFromFilters, hasActiveFilters, priceProblem, activeChips, cleanPrice};
