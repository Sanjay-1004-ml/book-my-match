'use strict';
/* Visual placeholders that need no image files: team-initial badges and sport-specific stadium artwork (inline SVG).
   Real images are used when an image_url exists; these are the fallback and are always rendered underneath.
   Pure functions (no DOM, no globals from other files), so they are unit-tested in Node. */
const artEsc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const TEAM_PALETTES = [['#ef4444', '#991b1b'], ['#f59e0b', '#b45309'], ['#22c55e', '#166534'], ['#14b8a6', '#115e59'], ['#3b82f6', '#1e40af'], ['#6366f1', '#3730a3'],
  ['#a855f7', '#6b21a8'], ['#ec4899', '#9d174d'], ['#0ea5e9', '#075985'], ['#84cc16', '#3f6212'], ['#f97316', '#9a3412'], ['#64748b', '#1e293b']];
function hashStr(s){ let h = 2166136261; for (const ch of String(s).toLowerCase()) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }

/* "Chennai Super Kings" -> "CS", "DC" -> "DC", "KKR" -> "KKR", "India" -> "IN" */
function teamInitials(name){
  const t = String(name || '').trim(); if (!t) return '?';
  const words = t.split(/\s+/).filter(Boolean);
  if (words.length === 1) return (t.length <= 3 ? t : t.slice(0, 2)).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}
function teamBadge(name, {size = 44} = {}){
  const [c1, c2] = TEAM_PALETTES[hashStr(name) % TEAM_PALETTES.length], ini = teamInitials(name), id = 'tb' + hashStr(name).toString(36);
  const fs = ini.length >= 3 ? 15 : 19;
  return `<svg class="tbadge" width="${size}" height="${size}" viewBox="0 0 48 48" role="img" aria-label="${artEsc(name)}"><defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs>`
    + `<path d="M24 2 43 9v14c0 11-8 19-19 23C13 42 5 34 5 23V9z" fill="url(#${id})" stroke="rgba(255,255,255,.35)" stroke-width="1.5"/>`
    + `<text x="24" y="29" text-anchor="middle" font-family="system-ui,Segoe UI,Roboto,sans-serif" font-weight="800" font-size="${fs}" fill="#fff">${artEsc(ini)}</text></svg>`;
}

const SPORT_ART = {
  cricket:   {bg: ['#14532d', '#16a34a'], key: 'cricket'},   football: {bg: ['#166534', '#22c55e'], key: 'football'},
  basketball:{bg: ['#9a3412', '#f97316'], key: 'basketball'}, hockey:   {bg: ['#1e3a8a', '#3b82f6'], key: 'hockey'},
  badminton: {bg: ['#4c1d95', '#8b5cf6'], key: 'badminton'},
};
function sportKey(name){ const n = String(name || '').toLowerCase(); return Object.keys(SPORT_ART).find(k => n.includes(k)) || 'default'; }
const L = 'fill="none" stroke="rgba(255,255,255,.7)" stroke-width="2"';
const FIELD = {
  cricket: `<ellipse cx="200" cy="100" rx="140" ry="64" ${L}/><ellipse cx="200" cy="100" rx="84" ry="38" ${L} stroke-dasharray="5 5"/><rect x="190" y="72" width="20" height="56" rx="2" fill="rgba(245,222,179,.85)"/><path d="M190 80h20M190 120h20" stroke="#fff" stroke-width="2"/>`,
  football: `<rect x="70" y="42" width="260" height="116" rx="4" ${L}/><path d="M200 42v116" ${L}/><circle cx="200" cy="100" r="20" ${L}/><rect x="70" y="72" width="34" height="56" ${L}/><rect x="296" y="72" width="34" height="56" ${L}/>`,
  basketball: `<rect x="76" y="42" width="248" height="116" rx="6" fill="rgba(255,255,255,.08)" ${L.replace('fill="none" ', '')}/><path d="M200 42v116" ${L}/><circle cx="200" cy="100" r="18" ${L}/><rect x="76" y="76" width="46" height="48" ${L}/><rect x="278" y="76" width="46" height="48" ${L}/><path d="M122 78a26 26 0 0 1 0 44M278 78a26 26 0 0 0 0 44" ${L}/>`,
  hockey: `<rect x="72" y="42" width="256" height="116" rx="24" ${L}/><path d="M200 42v116" ${L}/><circle cx="200" cy="100" r="16" ${L}/><path d="M72 76a34 34 0 0 1 34 24 34 34 0 0 1-34 24M328 76a34 34 0 0 0-34 24 34 34 0 0 0 34 24" ${L}/>`,
  badminton: `<rect x="108" y="38" width="184" height="124" ${L}/><path d="M108 100h184M108 70h184M108 130h184M200 38v124" ${L}/><path d="M200 94v12" stroke="#fff" stroke-width="4"/>`,
  default: `<ellipse cx="200" cy="100" rx="130" ry="62" ${L}/><ellipse cx="200" cy="100" rx="80" ry="36" ${L}/><circle cx="200" cy="100" r="8" fill="rgba(255,255,255,.7)"/>`,
};
/* Floodlit stadium with a sport-specific pitch. Fills its container (slice). Decorative: hidden from screen readers. */
function stadiumArt(sportName){
  const k = sportKey(sportName), [c1, c2] = (SPORT_ART[k] || {bg: ['#0f172a', '#334155']}).bg, id = 'sa' + k;
  const stands = [0, 1, 2].map(i => `<path d="M${-10 + i * 12} ${34 - i * 10}Q200 ${-6 - i * 8} ${410 - i * 12} ${34 - i * 10}" fill="none" stroke="rgba(255,255,255,${.16 - i * .04})" stroke-width="${10 - i * 2}"/>`).join('');
  const lights = [28, 372].map(x => `<g opacity=".9"><rect x="${x - 2}" y="18" width="4" height="40" fill="#cbd5e1"/><rect x="${x - 12}" y="10" width="24" height="10" rx="2" fill="#fde68a"/></g>`).join('');
  return `<svg class="art" viewBox="0 0 400 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs>`
    + `<rect width="400" height="200" fill="url(#${id})"/>${stands}<g>${FIELD[k] || FIELD.default}</g>${lights}<rect y="170" width="400" height="30" fill="rgba(0,0,0,.18)"/></svg>`;
}
/* The art is always drawn; a real image (if any) covers it and removes itself if it fails to load. */
function mediaBox(url, sportName, alt, cls = ''){
  return `<div class="media ${cls}">${stadiumArt(sportName)}${url ? `<img src="${artEsc(url)}" alt="${artEsc(alt || '')}" loading="lazy" onerror="this.remove()">` : ''}</div>`;
}

if (typeof module !== 'undefined' && module.exports) module.exports = {teamInitials, teamBadge, stadiumArt, mediaBox, sportKey, hashStr, artEsc};
