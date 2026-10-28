/* DEPOT demo backend
 * Lets the real DEPOT interface run with no server (for example on Vercel).
 * State lives in this browser. Every tab is a separate "phone": open the page
 * in two tabs to chat, share files and send SOS between them.
 * It is not the real hub: nothing leaves your browser.
 */
(() => {
'use strict';
const DB_KEY = 'depot.demo.db';
const MAX_FILE = 1.5 * 1024 * 1024;
const CHANNELS = ['general', 'medical', 'supplies', 'rescue'];
const STATUSES = ['unknown', 'safe', 'help', 'injured'];
const HEALTH = ['blood', 'allergies', 'conditions', 'medications', 'contact', 'notes'];
const T0 = Date.now();

/* ---- per-tab identity: depot.* keys use sessionStorage, the shared db uses localStorage ---- */
const sget = Storage.prototype.getItem, sset = Storage.prototype.setItem, srem = Storage.prototype.removeItem;
const perTab = k => typeof k === 'string' && k.startsWith('depot.') && !k.startsWith('depot.demo');
Storage.prototype.getItem = function (k) { return (this === localStorage && perTab(k)) ? sget.call(sessionStorage, k) : sget.call(this, k); };
Storage.prototype.setItem = function (k, v) { return (this === localStorage && perTab(k)) ? sset.call(sessionStorage, k, v) : sset.call(this, k, v); };
Storage.prototype.removeItem = function (k) { return (this === localStorage && perTab(k)) ? srem.call(sessionStorage, k) : srem.call(this, k); };

/* ---- helpers ---- */
const now = () => Date.now() / 1000;
const clean = (v, n) => String(v || '').replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '').trim().slice(0, n);
const cleanHealth = h => { h = h && typeof h === 'object' ? h : {}; return Object.fromEntries(HEALTH.map(k => [k, clean(h[k], k === 'notes' ? 400 : 160)])); };
const rand = () => [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, '0')).join('');
const fresh = () => ({ users: {}, messages: [], sos: [], seq: { m: 0, s: 0, u: 0 } });
function load() { try { return JSON.parse(sget.call(localStorage, DB_KEY)) || fresh(); } catch { return fresh(); } }
function save(db) {
  for (;;) {
    try { sset.call(localStorage, DB_KEY, JSON.stringify(db)); return true; }
    catch { const i = db.messages.findIndex(m => m.file); if (i < 0) return false; db.messages.splice(i, 1); }
  }
}
const pub = u => ({ uid: u.uid, name: u.name, status: u.status, ip: u.ip, health: u.health });

/* ---- data URLs are stored, blob URLs are served (browsers block navigating to data URLs) ---- */
const blobCache = {};
function dataToBlob(d) {
  const [h, b] = d.split(','); const mime = h.slice(5).split(';')[0];
  const bin = atob(b); const a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  return new Blob([a], { type: mime });
}
function out(m) {
  if (!m.file || !m.file.url.startsWith('data:')) return m;
  if (!blobCache[m.id]) blobCache[m.id] = URL.createObjectURL(dataToBlob(m.file.url));
  return { ...m, file: { ...m.file, url: blobCache[m.id] } };
}

/* ---- live events between tabs ---- */
const bc = 'BroadcastChannel' in window ? new BroadcastChannel('depot-demo') : null;
const sources = new Set();
const alive = {};
let myUid = null, lastPresence = '';
const isAlive = uid => Date.now() - (alive[uid] || 0) < 9000;
const presence = () => { const uids = Object.keys(alive).filter(isAlive).sort(); return { online: uids.length, uids }; };
function deliver(ev, data) { if (ev === 'message') data = out(data); sources.forEach(s => s._emit(ev, data)); }
function emit(ev, data) { if (bc) bc.postMessage({ ev, data }); deliver(ev, data); }
function checkPresence() { const p = presence(), k = JSON.stringify(p); if (k !== lastPresence) { lastPresence = k; deliver('presence', p); } }
function heartbeat() { if (!myUid) return; alive[myUid] = Date.now(); if (bc) bc.postMessage({ ev: '_hb', data: { uid: myUid } }); }
if (bc) bc.onmessage = e => {
  const { ev, data } = e.data;
  if (ev === '_hb') { alive[data.uid] = Date.now(); checkPresence(); }
  else if (ev === '_bye') { delete alive[data.uid]; checkPresence(); }
  else if (ev === '_ping') heartbeat();
  else deliver(ev, data);
};
setInterval(() => { heartbeat(); checkPresence(); }, 3000);
window.addEventListener('pagehide', () => { if (bc && myUid) bc.postMessage({ ev: '_bye', data: { uid: myUid } }); });

class FakeEventSource {
  constructor(url) {
    this.L = {}; this.readyState = 0;
    const token = new URL(url, location.origin).searchParams.get('token');
    const u = load().users[token];
    if (!u) { setTimeout(() => { this.readyState = 2; this.onerror && this.onerror(); }, 50); return; }
    myUid = u.uid; sources.add(this);
    setTimeout(() => {
      this.readyState = 1; alive[myUid] = Date.now();
      if (bc) { bc.postMessage({ ev: '_hb', data: { uid: myUid } }); bc.postMessage({ ev: '_ping', data: {} }); }
      this.onopen && this.onopen();
      this._emit('hello', presence()); lastPresence = JSON.stringify(presence());
    }, 30);
  }
  addEventListener(n, f) { (this.L[n] = this.L[n] || []).push(f); }
  close() { sources.delete(this); this.readyState = 2; }
  _emit(n, d) { (this.L[n] || []).forEach(f => f({ data: JSON.stringify(d) })); }
}
window.EventSource = FakeEventSource;

/* ---- spam guard (same rules as the real server) ---- */
const gs = {};
function guard(uid, text) {
  const t = now(); const s = gs[uid] = gs[uid] || { times: [], last: null, rep: 0, strikes: 0, until: 0, struck: 0 };
  if (t < s.until) return { ok: false, secs: Math.ceil(s.until - t), why: 'You are in a timeout.' };
  if (s.strikes && t - s.struck > 900) s.strikes = 0;
  s.times = s.times.filter(x => t - x < 8);
  let why = null;
  if (s.times.length >= 6) why = 'Sending too fast.';
  if (text) {
    const n = text.trim().toLowerCase(); s.rep = n === s.last ? s.rep + 1 : 0; s.last = n;
    if (s.rep >= 2) why = why || 'Repeating the same message.';
    if (/(.)\1{14,}/.test(n)) why = why || 'Flooding with repeated characters.';
  }
  if (why) {
    s.strikes++; s.struck = t; const secs = [10, 30, 60, 300, 600][Math.min(s.strikes - 1, 4)];
    s.until = t + secs; s.rep = 0; s.times = []; return { ok: false, secs, why };
  }
  s.times.push(t); return { ok: true };
}

/* ---- messages, SOS ---- */
function addMsg(db, channel, uid, name, kind, body, file) {
  const m = { id: ++db.seq.m, channel, uid, name, kind, body: body || '', ts: now() };
  if (file) m.file = file;
  db.messages.push(m);
  if (db.messages.length > 400) db.messages.shift();
  return m;
}
const sysMsg = (db, text) => addMsg(db, 'general', 'system', 'DEPOT', 'system', text);
const sosJson = r => ({ id: r.id, uid: r.uid, name: r.name, ip: r.ip, note: r.note || '', ts: r.ts, resolved: !!r.resolved, health: r.health, responders: r.responders });
const activeSos = db => db.sos.filter(s => !s.resolved).sort((a, b) => b.id - a.id);

function fakeSystem() {
  const t = (Date.now() - T0) / 1000;
  return { temp_c: +(46 + 4 * Math.sin(t / 30)).toFixed(1), load: [+(0.3 + 0.15 * Math.sin(t / 20)).toFixed(2), 0.25, 0.2], cpus: 4,
    mem_total: 2147483648, mem_used: 760000000 + Math.round(2e7 * Math.sin(t / 40)), disk_total: 31914983424, disk_used: 4400000000,
    uptime: Math.round(t + 5 * 3600) };
}

function route(method, url, body, token) {
  const u = new URL(url, location.origin), p = u.pathname, q = u.searchParams;
  const db = load(); body = body || {};
  if (p === '/api/ping') return [200, { ok: true, name: 'DEPOT demo', time: now() }];
  if (p === '/api/join' && method === 'POST') {
    const name = clean(body.name, 24);
    if (!name) return [400, { error: 'Enter a display name.' }];
    const health = cleanHealth(body.health); let t = body.token, user = t && db.users[t];
    if (user) { user.name = name; user.health = health; user.last_seen = now(); }
    else { t = rand(); db.seq.u++; user = { token: t, uid: t.slice(0, 12), name, health, status: 'unknown', ip: '10.42.0.' + (50 + db.seq.u), last_seen: now() }; db.users[t] = user; }
    save(db); emit('people', {});
    return [200, { token: t, user: pub(user), channels: CHANNELS }];
  }
  const user = db.users[token];
  if (!user) return [401, { error: 'Join the hub first.' }];

  if (p === '/api/me') return [200, { user: pub(user), channels: CHANNELS, active_sos: activeSos(db).map(sosJson) }];

  if (p === '/api/status' && method === 'POST') {
    if (!STATUSES.includes(body.status)) return [400, { error: 'Unknown status.' }];
    const changed = user.status !== body.status; user.status = body.status; user.last_seen = now();
    const label = { safe: 'is safe', help: 'needs help', injured: 'is injured' }[body.status];
    const m = label && changed ? sysMsg(db, `${user.name} ${label}.`) : null;
    save(db); if (m) emit('message', m); emit('people', {});
    return [200, { status: body.status }];
  }

  if (p === '/api/messages' && method === 'GET') {
    const ch = q.get('channel') || 'general'; if (!CHANNELS.includes(ch)) return [400, { error: 'Unknown channel.' }];
    const after = parseInt(q.get('after') || '0', 10);
    let list = db.messages.filter(m => m.channel === ch);
    list = after ? list.filter(m => m.id > after) : list.slice(-100);
    return [200, { messages: list.map(out) }];
  }
  if (p === '/api/messages' && method === 'POST') {
    const ch = body.channel || 'general', text = clean(body.text, 2000);
    if (!CHANNELS.includes(ch)) return [400, { error: 'Unknown channel.' }];
    if (!text) return [400, { error: 'Message is empty.' }];
    const g = guard(user.uid, text); if (!g.ok) return [429, { error: g.why, retry_after: g.secs }];
    user.last_seen = now(); const m = addMsg(db, ch, user.uid, user.name, 'text', text);
    if (!save(db)) return [507, { error: 'Demo storage is full.' }];
    emit('message', m); return [200, { message: m }];
  }

  if (p === '/api/files' && method === 'GET') {
    const files = db.messages.filter(m => m.kind === 'file').reverse().slice(0, 300).map(out);
    const total = db.messages.filter(m => m.file).reduce((a, m) => a + (m.file.size || 0), 0);
    return [200, { files, total_bytes: total }];
  }
  const del = p.match(/^\/api\/files\/(\d+)$/);
  if (del && method === 'DELETE') {
    const id = +del[1], i = db.messages.findIndex(m => m.id === id && m.file);
    if (i < 0) return [404, { error: 'Not found.' }];
    if (db.messages[i].uid !== user.uid) return [403, { error: 'Only the person who shared this can delete it.' }];
    db.messages.splice(i, 1); save(db); emit('delete', { id }); return [200, { ok: true }];
  }

  if (p === '/api/sos' && method === 'GET') return [200, { active: activeSos(db).map(sosJson) }];
  if (p === '/api/sos' && method === 'POST') {
    let s = activeSos(db).find(x => x.uid === user.uid);
    if (s) return [200, { sos: sosJson(s) }];
    s = { id: ++db.seq.s, uid: user.uid, name: user.name, ip: user.ip, note: clean(body.note, 200), health: user.health, responders: [], resolved: false, ts: now() };
    db.sos.push(s); user.status = 'help';
    const m = sysMsg(db, `SOS raised by ${user.name} (${user.ip}).`);
    save(db); emit('sos', sosJson(s)); emit('message', m); emit('people', {});
    return [200, { sos: sosJson(s) }];
  }
  const act = p.match(/^\/api\/sos\/(\d+)\/(respond|resolve)$/);
  if (act && method === 'POST') {
    const s = db.sos.find(x => x.id === +act[1] && !x.resolved);
    if (!s) return [404, { error: 'This SOS is already closed.' }];
    if (act[2] === 'respond') {
      if (s.uid === user.uid) return [400, { error: 'You cannot respond to your own SOS.' }];
      let m = null;
      if (!s.responders.some(r => r.uid === user.uid)) { s.responders.push({ uid: user.uid, name: user.name }); m = sysMsg(db, `${user.name} is on the way to ${s.name}.`); }
      save(db); if (m) emit('message', m); emit('sos_update', sosJson(s)); return [200, { sos: sosJson(s) }];
    }
    if (s.uid !== user.uid && !s.responders.some(r => r.uid === user.uid)) return [403, { error: 'Only the sender or a responder can close an SOS.' }];
    s.resolved = true; const m = sysMsg(db, `SOS from ${s.name} was closed by ${user.name}.`);
    save(db); emit('sos_update', sosJson(s)); emit('message', m); return [200, { sos: sosJson(s) }];
  }

  if (p === '/api/network') {
    const users = Object.values(db.users).map(x => ({ uid: x.uid, name: x.name, status: x.status, ip: x.ip,
      last_seen: Math.max(x.last_seen || 0, (alive[x.uid] || 0) / 1000), online: isAlive(x.uid) || x.uid === user.uid })).sort((a, b) => b.last_seen - a.last_seen);
    const devices = users.filter(x => x.online).map(x => ({ ip: x.ip, mac: '', host: '', user: x.name }));
    return [200, { users, devices, system: fakeSystem(), hub_ip: '10.42.0.1', now: now() }];
  }
  return [404, { error: 'Not found.' }];
}

async function doUpload(fd, token) {
  let db = load(); const user = db.users[token];
  if (!user) return [401, { error: 'Join the hub first.' }];
  const f = fd.get('file'), channel = fd.get('channel') || 'general'; let kind = fd.get('kind') || 'file';
  if (!CHANNELS.includes(channel)) return [400, { error: 'Unknown channel.' }];
  if (!f || !f.size) return [400, { error: 'That file is empty.' }];
  if (kind !== 'voice') kind = 'file';
  if (f.size > MAX_FILE) return [413, { error: 'The demo limit is 1.5 MB per file. The real hub allows 25 MB.' }];
  const g = guard(user.uid); if (!g.ok) return [429, { error: g.why, retry_after: g.secs }];
  const dataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
  let mime = (f.type || 'application/octet-stream').split(';')[0].toLowerCase(), name = clean(f.name, 120) || 'file';
  if (kind === 'voice') { if (!mime.startsWith('audio/')) mime = 'audio/webm'; name = 'voice-note.' + (mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm'); }
  else if (mime === 'application/octet-stream') {
    const ext = (name.split('.').pop() || '').toLowerCase();
    mime = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', pdf: 'application/pdf', txt: 'text/plain', mp4: 'video/mp4' }[ext] || mime;
  }
  db = load();
  const m = addMsg(db, channel, user.uid, user.name, kind, clean(fd.get('text'), 500),
    { url: dataUrl, name, mime, size: f.size, dur: parseFloat(fd.get('duration')) || null });
  if (!save(db)) return [507, { error: 'Demo storage is full. Delete some files and try again.' }];
  emit('message', m); return [200, { message: out(m) }];
}

/* ---- network layer replacements ---- */
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  if (!/^(https?:\/\/[^/]+)?\/api\//.test(url)) return realFetch(input, init);
  const headers = init.headers || {}; let body = null;
  if (init.body) { try { body = JSON.parse(init.body); } catch {} }
  await new Promise(r => setTimeout(r, 15));
  const [status, data] = route((init.method || 'GET').toUpperCase(), url, body, headers['X-Depot-Token'] || null);
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
};
class FakeXHR {
  constructor() { this.upload = {}; this.headers = {}; this.status = 0; this.responseText = ''; }
  open(m, u) { this.m = m; this.u = u; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  send(fd) {
    const total = (fd.get('file') || {}).size || 1; let loaded = 0;
    const tick = setInterval(() => {
      loaded = Math.min(total, loaded + total / 4);
      this.upload.onprogress && this.upload.onprogress({ lengthComputable: true, loaded, total });
      if (loaded >= total) {
        clearInterval(tick);
        doUpload(fd, this.headers['X-Depot-Token']).then(([s, d]) => { this.status = s; this.responseText = JSON.stringify(d); this.onload && this.onload(); })
          .catch(() => this.onerror && this.onerror());
      }
    }, 50);
  }
}
window.XMLHttpRequest = FakeXHR;
})();
