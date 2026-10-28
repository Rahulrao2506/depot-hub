#!/usr/bin/env python3
"""
DEPOT - Offline Communication & File-Sharing Hub
------------------------------------------------
One Flask process that runs on a Raspberry Pi (or any laptop for testing):

  * chat channels with live push (Server-Sent Events)
  * file / photo sharing and voice notes (stored on local disk)
  * emergency SOS with a health card and "I'm coming" responders
  * safety check-ins (safe / need help / injured)
  * live network monitor (people, Wi-Fi devices, Pi temperature/load/memory/disk)
  * rule-based spam guard with escalating timeouts (SOS is never throttled)

No internet is needed. The only dependency is Flask.

    python server.py                       # http://localhost:8080
    python server.py --port 80 --https-port 443   # on the Pi (needs root or CAP_NET_BIND_SERVICE)
"""
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
DATA = Path(os.environ.get("DEPOT_DATA", BASE / "data"))
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "depot.db"

MAX_UPLOAD = 25 * 1024 * 1024
MIN_FREE_BYTES = 50 * 1024 * 1024
CHANNELS = ["general", "medical", "supplies", "rescue"]
STATUSES = ["unknown", "safe", "help", "injured"]
HEALTH_FIELDS = ["blood", "allergies", "conditions", "medications", "contact", "notes"]
HUB_IP = os.environ.get("DEPOT_IP", "10.42.0.1")

# --- Cloud / public-hub settings (all optional; the Pi needs none of them) ---
# DEPOT_PIN        room PIN. If set, people must enter it to join.
# DEPOT_PUBLIC=1   hosted on the internet: hide IP addresses, never let a client
#                  change the server clock, show no Wi-Fi device list.
# DEPOT_PROXY=1    server sits behind a reverse proxy (Render, Fly, Railway):
#                  trust X-Forwarded-* headers for client IP and https.
# DEPOT_MAX_STORAGE_MB  total upload quota (0 = unlimited, only disk space check)
# DEPOT_RATE_LIMIT=0    turn the per-IP rate limits off
PIN = os.environ.get("DEPOT_PIN", "").strip()
PUBLIC = os.environ.get("DEPOT_PUBLIC", "") == "1"
MAX_STORAGE = int(os.environ.get("DEPOT_MAX_STORAGE_MB", "0") or 0) * 1024 * 1024
RATE_ON = os.environ.get("DEPOT_RATE_LIMIT", "1") != "0"
HUB_NAME = os.environ.get("DEPOT_NAME", "DEPOT")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 512 * 1024
if os.environ.get("DEPOT_PROXY", "") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# --------------------------------------------------------------------------
# Clock: a Pi has no battery-backed clock, so after a power loss it may think
# it is a different day. We learn an offset from the first phone that joins.
# --------------------------------------------------------------------------
CLOCK = {"offset": 0.0}


def now():
    return time.time() + CLOCK["offset"]


def client_ip():
    return request.remote_addr or ""


def shown_ip(ip):
    """On a public hub, never show people's IP addresses to each other."""
    return "" if PUBLIC else (ip or "")


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    token TEXT PRIMARY KEY,
    uid TEXT UNIQUE,
    name TEXT,
    health TEXT DEFAULT '{}',
    status TEXT DEFAULT 'unknown',
    ip TEXT,
    created REAL,
    last_seen REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT,
    uid TEXT,
    name TEXT,
    kind TEXT,          -- text | file | voice | system
    body TEXT,
    stored TEXT,
    fname TEXT,
    mime TEXT,
    size INTEGER,
    dur REAL,
    ts REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, id);
CREATE TABLE IF NOT EXISTS sos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT,
    name TEXT,
    ip TEXT,
    note TEXT,
    health TEXT,
    responders TEXT DEFAULT '[]',
    resolved INTEGER DEFAULT 0,
    ts REAL
);
"""


def run(sql, args=(), fetch="all"):
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        if fetch == "all":
            res = [dict(r) for r in cur.fetchall()]
        elif fetch == "one":
            r = cur.fetchone()
            res = dict(r) if r else None
        elif fetch == "id":
            res = cur.lastrowid
        else:
            res = None
        con.commit()
        return res
    finally:
        con.close()


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.commit()
    con.close()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def clean(value, limit):
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or "")).strip()
    return text[:limit]


def clean_health(h):
    h = h if isinstance(h, dict) else {}
    return {k: clean(h.get(k, ""), 400 if k == "notes" else 160) for k in HEALTH_FIELDS}


def make_uid(token):
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def current_user():
    token = request.headers.get("X-Depot-Token") or request.args.get("token") or request.cookies.get("depot_token")
    if not token:
        return None
    u = run("SELECT * FROM users WHERE token=?", (token,), "one")
    if not u:
        return None
    t = now()
    if t - (u["last_seen"] or 0) > 20 or u["ip"] != client_ip():
        run("UPDATE users SET last_seen=?, ip=? WHERE token=?", (t, client_ip(), token), "none")
        u["ip"] = client_ip()
    return u


def with_cookie(resp, token):
    resp.set_cookie("depot_token", token, max_age=60 * 60 * 24 * 365, httponly=True,
                    samesite="Lax", secure=request.is_secure)
    return resp


def auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return jsonify(error="Join the hub first."), 401
        return fn(u, *args, **kwargs)

    return wrapper


def public_user(u):
    return {
        "uid": u["uid"],
        "name": u["name"],
        "status": u["status"],
        "ip": shown_ip(u["ip"]),
        "health": json.loads(u["health"] or "{}"),
    }


def msg_json(r):
    out = {
        "id": r["id"],
        "channel": r["channel"],
        "uid": r["uid"],
        "name": r["name"],
        "kind": r["kind"],
        "body": r["body"] or "",
        "ts": r["ts"],
    }
    if r["kind"] in ("file", "voice"):
        out["file"] = {
            "url": "/uploads/" + r["stored"],
            "name": r["fname"],
            "mime": r["mime"],
            "size": r["size"],
            "dur": r["dur"],
        }
    return out


# --------------------------------------------------------------------------
# Live push (Server-Sent Events)
# --------------------------------------------------------------------------
clients = {}
clients_lock = threading.Lock()


def broadcast(event, data):
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with clients_lock:
        for c in list(clients.values()):
            try:
                c["q"].put_nowait(payload)
            except queue.Full:
                pass


def presence():
    with clients_lock:
        uids = sorted({c["uid"] for c in clients.values()})
    return {"online": len(uids), "uids": uids}


def add_message(channel, uid, name, kind, body="", **file):
    mid = run(
        "INSERT INTO messages(channel,uid,name,kind,body,stored,fname,mime,size,dur,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            channel, uid, name, kind, body,
            file.get("stored"), file.get("fname"), file.get("mime"), file.get("size"), file.get("dur"),
            now(),
        ),
        "id",
    )
    row = run("SELECT * FROM messages WHERE id=?", (mid,), "one")
    m = msg_json(row)
    broadcast("message", m)
    return m


def system_message(text, channel="general"):
    return add_message(channel, "system", "DEPOT", "system", text)


# --------------------------------------------------------------------------
# Spam guard: bursts, repeats and character floods -> escalating timeouts
# --------------------------------------------------------------------------
class SpamGuard:
    BURST_COUNT = 6
    BURST_SECONDS = 8
    PENALTIES = [10, 30, 60, 300, 600]

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {}

    def check(self, uid, text=None):
        t = time.time()
        with self.lock:
            s = self.state.setdefault(
                uid, {"times": [], "last": None, "repeat": 0, "strikes": 0, "until": 0, "struck": 0}
            )
            if t < s["until"]:
                return False, int(s["until"] - t) + 1, "You are in a timeout."
            if s["strikes"] and t - s["struck"] > 900:
                s["strikes"] = 0
            s["times"] = [x for x in s["times"] if t - x < self.BURST_SECONDS]
            reason = None
            if len(s["times"]) >= self.BURST_COUNT:
                reason = "Sending too fast."
            if text:
                norm = text.strip().lower()
                s["repeat"] = s["repeat"] + 1 if norm == s["last"] else 0
                s["last"] = norm
                if s["repeat"] >= 2:
                    reason = reason or "Repeating the same message."
                if re.search(r"(.)\1{14,}", norm):
                    reason = reason or "Flooding with repeated characters."
            if reason:
                s["strikes"] += 1
                s["struck"] = t
                secs = self.PENALTIES[min(s["strikes"] - 1, len(self.PENALTIES) - 1)]
                s["until"] = t + secs
                s["repeat"] = 0
                s["times"] = []
                return False, secs, reason
            s["times"].append(t)
            return True, 0, ""


guard = SpamGuard()


def throttled(secs, reason):
    return jsonify(error=reason, retry_after=secs), 429


# --------------------------------------------------------------------------
# Per-IP rate limits (sliding window). SOS endpoints are never limited.
# --------------------------------------------------------------------------
class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits = {}
        self.last_prune = time.time()

    def hit(self, key, limit, window):
        t = time.time()
        with self.lock:
            if t - self.last_prune > 300:
                self.hits = {k: [x for x in v if t - x < 3600] for k, v in self.hits.items()}
                self.hits = {k: v for k, v in self.hits.items() if v}
                self.last_prune = t
            q = [x for x in self.hits.get(key, []) if t - x < window]
            if len(q) >= limit:
                self.hits[key] = q
                return False, int(window - (t - q[0])) + 1
            q.append(t)
            self.hits[key] = q
            return True, 0

    def count(self, key, window):
        t = time.time()
        with self.lock:
            return len([x for x in self.hits.get(key, []) if t - x < window])


limiter = RateLimiter()
# (path prefix or exact, method or None, limit, window seconds, bucket)
RATE_RULES = [
    ("/api/join", "POST", 15, 600, "join"),
    ("/api/upload", "POST", 12, 60, "upload"),
    ("/api/messages", "POST", 60, 60, "msg"),
    ("/api/files", "DELETE", 30, 60, "del"),
]
GLOBAL_LIMIT = (600, 60)  # requests per window per IP, a safety net only


@app.before_request
def rate_limit():
    if not RATE_ON:
        return None
    path = request.path
    if path.startswith("/api/sos") or path in ("/healthz", "/api/stream") or not path.startswith("/api/"):
        return None
    ip = client_ip()
    for prefix, method, limit, window, bucket in RATE_RULES:
        if path.startswith(prefix) and (method is None or request.method == method):
            ok, wait = limiter.hit(f"{bucket}:{ip}", limit, window)
            if not ok:
                return jsonify(error="Too many requests from this device. Please wait a moment.", retry_after=wait), 429
    ok, wait = limiter.hit(f"all:{ip}", *GLOBAL_LIMIT)
    if not ok:
        return jsonify(error="Too many requests from this device. Please wait a moment.", retry_after=wait), 429
    return None


# --------------------------------------------------------------------------
# Pages & captive portal
# --------------------------------------------------------------------------
@app.get("/")
def index():
    resp = send_from_directory(WEB, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _captive():
    return redirect("/", 302)


for i, path in enumerate([
    "/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html",
    "/connecttest.txt", "/ncsi.txt", "/success.txt", "/canonical.html",
]):
    app.add_url_rule(path, endpoint=f"captive_{i}", view_func=_captive)


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith(("/api/", "/uploads/")):
        return jsonify(error="Not found."), 404
    return redirect("/", 302)


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"File is too large. The limit is {MAX_UPLOAD // (1024 * 1024)} MB."), 413


@app.get("/healthz")
def healthz():
    try:
        run("SELECT 1", (), "one")
        return jsonify(ok=True)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc)), 503


@app.get("/api/config")
def config():
    return jsonify(pin_required=bool(PIN), name=HUB_NAME, public=PUBLIC)


@app.get("/api/ping")
def ping():
    return jsonify(ok=True, name="DEPOT", time=now())


# --------------------------------------------------------------------------
# Join / profile
# --------------------------------------------------------------------------
@app.post("/api/join")
def join():
    d = request.get_json(silent=True) or {}
    name = clean(d.get("name"), 24)
    if not name:
        return jsonify(error="Enter a display name."), 400

    token = d.get("token")
    user = run("SELECT * FROM users WHERE token=?", (token,), "one") if token else None

    # Room PIN: required for new devices (a device that already holds a valid token is already in).
    if PIN and not user:
        ip = client_ip()
        if limiter.count(f"pinfail:{ip}", 900) >= 5:
            return throttled(300, "Too many wrong PINs. Wait 5 minutes and try again.")
        given = clean(d.get("pin"), 64)
        if not hmac.compare_digest(given.encode(), PIN.encode()):
            limiter.hit(f"pinfail:{ip}", 10**6, 900)
            return jsonify(error="Wrong PIN." if given else "This hub needs a PIN.", pin_required=True), 403

    # Learn the real time from a phone if the Pi's clock is far off.
    # Never on a public hub: the server clock is right and a stranger must not move it.
    if not PUBLIC:
        try:
            client_time = float(d.get("client_time"))
            if abs(client_time - now()) > 120:
                CLOCK["offset"] += client_time - now()
        except (TypeError, ValueError):
            pass

    health = json.dumps(clean_health(d.get("health")))
    t = now()
    if user:
        run("UPDATE users SET name=?, health=?, ip=?, last_seen=? WHERE token=?",
            (name, health, client_ip(), t, token), "none")
    else:
        token = uuid.uuid4().hex
        run("INSERT INTO users(token,uid,name,health,status,ip,created,last_seen) VALUES(?,?,?,?,?,?,?,?)",
            (token, make_uid(token), name, health, "unknown", client_ip(), t, t), "none")
    user = run("SELECT * FROM users WHERE token=?", (token,), "one")
    broadcast("people", {})
    return with_cookie(jsonify(token=token, user=public_user(user), channels=CHANNELS), token)


@app.get("/api/me")
@auth
def me(u):
    return with_cookie(jsonify(user=public_user(u), channels=CHANNELS,
                               active_sos=[sos_json(r) for r in active_sos()]), u["token"])


@app.post("/api/status")
@auth
def set_status(u):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in STATUSES:
        return jsonify(error="Unknown status."), 400
    run("UPDATE users SET status=? WHERE token=?", (status, u["token"]), "none")
    if status in ("safe", "help", "injured") and u["status"] != status:
        label = {"safe": "is safe", "help": "needs help", "injured": "is injured"}[status]
        system_message(f"{u['name']} {label}.", "general")
    broadcast("people", {})
    return jsonify(status=status)


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------
@app.get("/api/messages")
@auth
def list_messages(u):
    channel = request.args.get("channel", "general")
    if channel not in CHANNELS:
        return jsonify(error="Unknown channel."), 400
    after = request.args.get("after", type=int)
    limit = min(request.args.get("limit", 100, type=int), 300)
    if after:
        rows = run("SELECT * FROM messages WHERE channel=? AND id>? ORDER BY id ASC LIMIT ?",
                   (channel, after, limit))
    else:
        rows = run("SELECT * FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?", (channel, limit))[::-1]
    return jsonify(messages=[msg_json(r) for r in rows])


@app.post("/api/messages")
@auth
def post_message(u):
    d = request.get_json(silent=True) or {}
    channel = d.get("channel", "general")
    text = clean(d.get("text"), 2000)
    if channel not in CHANNELS:
        return jsonify(error="Unknown channel."), 400
    if not text:
        return jsonify(error="Message is empty."), 400
    ok, secs, why = guard.check(u["uid"], text)
    if not ok:
        return throttled(secs, why)
    return jsonify(message=add_message(channel, u["uid"], u["name"], "text", text))


# --------------------------------------------------------------------------
# Files and voice notes
# --------------------------------------------------------------------------
VOICE_EXT = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
             "audio/wav": ".wav", "audio/x-m4a": ".m4a", "audio/aac": ".aac"}


BLOCKED_EXT = {
    ".exe", ".dll", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".cpl", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".ps1", ".psm1", ".sh", ".bash", ".jar", ".app", ".dmg", ".reg", ".lnk", ".hta",
    ".html", ".htm", ".xhtml", ".svg", ".php", ".py", ".pl", ".rb",
}


def sniff_kind(head):
    """Identify common media containers from the first bytes. Returns image/audio/video/av/'binary'/None."""
    if head[:2] == b"MZ" or head[:4] == b"\x7fELF" or head[:4] in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe"):
        return "binary"
    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:6] in (b"GIF87a", b"GIF89a") or head[:2] == b"BM":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif"):
            return "image"
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return "audio"
        return "av"  # mp4 / mov / 3gp: may carry audio or video
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "av"  # webm / mkv
    if head[:4] == b"OggS":
        return "av"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xf1", b"\xff\xf9"):
        return "audio"
    if head[:6] == b"#!AMR\n" or head[:4] == b"fLaC":
        return "audio"
    return None


def check_upload(path, mime, ext):
    """Return an error string if the saved file is not acceptable, else None."""
    if ext in BLOCKED_EXT:
        return f"Files of type {ext} are not allowed on this hub."
    with open(path, "rb") as fh:
        head = fh.read(16)
    kind = sniff_kind(head)
    if kind == "binary":
        return "Programs and executables are not allowed on this hub."
    major = mime.split("/")[0]
    if major in ("image", "audio", "video"):
        ok = kind == major or (kind == "av" and major in ("audio", "video"))
        if not ok:
            return "That file's contents do not match its type."
    return None


@app.post("/api/upload")
@auth
def upload(u):
    f = request.files.get("file")
    channel = request.form.get("channel", "general")
    kind = request.form.get("kind", "file")
    if channel not in CHANNELS:
        return jsonify(error="Unknown channel."), 400
    if not f or not f.filename:
        return jsonify(error="No file received."), 400
    if kind not in ("file", "voice"):
        kind = "file"
    if shutil.disk_usage(DATA).free < MIN_FREE_BYTES:
        return jsonify(error="The hub's storage is almost full. Delete old files first."), 507
    ok, secs, why = guard.check(u["uid"])
    if not ok:
        return throttled(secs, why)

    orig = clean(os.path.basename(f.filename), 120) or "file"
    mime = (f.mimetype or "").split(";")[0].strip().lower() or mimetypes.guess_type(orig)[0] or "application/octet-stream"
    if kind == "voice":
        if not mime.startswith("audio/"):
            mime = "audio/webm" if "webm" in mime else "audio/mp4"
        ext = VOICE_EXT.get(mime, ".webm")
        orig = "voice-note" + ext
    else:
        ext = os.path.splitext(orig)[1].lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
            ext = mimetypes.guess_extension(mime) or ""
    stored = uuid.uuid4().hex + ext
    path = UPLOADS / stored
    f.save(path)
    size = path.stat().st_size
    if size == 0:
        path.unlink(missing_ok=True)
        return jsonify(error="That file is empty."), 400
    problem = check_upload(path, mime, ext)
    if problem:
        path.unlink(missing_ok=True)
        return jsonify(error=problem), 415
    if MAX_STORAGE:
        used = run("SELECT COALESCE(SUM(size),0) AS n FROM messages WHERE kind IN ('file','voice')", (), "one")["n"]
        if used > MAX_STORAGE:
            path.unlink(missing_ok=True)
            return jsonify(error="The hub's file storage quota is full. Delete old files first."), 507
    try:
        dur = float(request.form.get("duration", 0)) or None
    except ValueError:
        dur = None
    m = add_message(channel, u["uid"], u["name"], kind, clean(request.form.get("text"), 500),
                    stored=stored, fname=orig, mime=mime, size=size, dur=dur)
    return jsonify(message=m)


@app.get("/uploads/<name>")
def serve_upload(name):
    if not current_user():
        return jsonify(error="Join the hub first."), 401
    if not re.fullmatch(r"[0-9a-f]{32}(\.[a-z0-9]{1,8})?", name):
        return jsonify(error="Not found."), 404
    row = run("SELECT fname, mime FROM messages WHERE stored=?", (name,), "one")
    if not row:
        return jsonify(error="Not found."), 404
    mime = row["mime"] or "application/octet-stream"
    inline = mime.startswith(("image/", "audio/", "video/")) and mime != "image/svg+xml"
    resp = send_from_directory(UPLOADS, name, mimetype=mime, as_attachment=not inline,
                               download_name=row["fname"], conditional=True)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.get("/api/files")
@auth
def list_files(u):
    rows = run("SELECT * FROM messages WHERE kind='file' ORDER BY id DESC LIMIT 300")
    total = run("SELECT COALESCE(SUM(size),0) AS n FROM messages WHERE kind IN ('file','voice')", (), "one")["n"]
    return jsonify(files=[msg_json(r) for r in rows], total_bytes=total)


@app.delete("/api/files/<int:mid>")
@auth
def delete_file(u, mid):
    row = run("SELECT * FROM messages WHERE id=? AND kind IN ('file','voice')", (mid,), "one")
    if not row:
        return jsonify(error="Not found."), 404
    if row["uid"] != u["uid"]:
        return jsonify(error="Only the person who shared this can delete it."), 403
    (UPLOADS / row["stored"]).unlink(missing_ok=True)
    run("DELETE FROM messages WHERE id=?", (mid,), "none")
    broadcast("delete", {"id": mid})
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# SOS
# --------------------------------------------------------------------------
def sos_json(r):
    return {
        "id": r["id"], "uid": r["uid"], "name": r["name"], "ip": shown_ip(r["ip"]), "note": r["note"] or "",
        "ts": r["ts"], "resolved": bool(r["resolved"]),
        "health": json.loads(r["health"] or "{}"),
        "responders": json.loads(r["responders"] or "[]"),
    }


def active_sos():
    return run("SELECT * FROM sos WHERE resolved=0 ORDER BY id DESC")


@app.get("/api/sos")
@auth
def get_sos(u):
    return jsonify(active=[sos_json(r) for r in active_sos()])


@app.post("/api/sos")
@auth
def raise_sos(u):
    existing = run("SELECT * FROM sos WHERE uid=? AND resolved=0", (u["uid"],), "one")
    if existing:
        return jsonify(sos=sos_json(existing))
    note = clean((request.get_json(silent=True) or {}).get("note"), 200)
    sid = run("INSERT INTO sos(uid,name,ip,note,health,responders,resolved,ts) VALUES(?,?,?,?,?,?,0,?)",
              (u["uid"], u["name"], u["ip"], note, u["health"], "[]", now()), "id")
    row = run("SELECT * FROM sos WHERE id=?", (sid,), "one")
    run("UPDATE users SET status='help' WHERE token=?", (u["token"],), "none")
    broadcast("sos", sos_json(row))
    where = f" ({u['ip']})" if shown_ip(u["ip"]) else ""
    system_message(f"SOS raised by {u['name']}{where}.", "general")
    broadcast("people", {})
    return jsonify(sos=sos_json(row))


@app.post("/api/sos/<int:sid>/respond")
@auth
def respond_sos(u, sid):
    row = run("SELECT * FROM sos WHERE id=? AND resolved=0", (sid,), "one")
    if not row:
        return jsonify(error="This SOS is already closed."), 404
    if row["uid"] == u["uid"]:
        return jsonify(error="You cannot respond to your own SOS."), 400
    responders = json.loads(row["responders"] or "[]")
    if not any(r["uid"] == u["uid"] for r in responders):
        responders.append({"uid": u["uid"], "name": u["name"]})
        run("UPDATE sos SET responders=? WHERE id=?", (json.dumps(responders), sid), "none")
        system_message(f"{u['name']} is on the way to {row['name']}.", "general")
    row = run("SELECT * FROM sos WHERE id=?", (sid,), "one")
    broadcast("sos_update", sos_json(row))
    return jsonify(sos=sos_json(row))


@app.post("/api/sos/<int:sid>/resolve")
@auth
def resolve_sos(u, sid):
    row = run("SELECT * FROM sos WHERE id=? AND resolved=0", (sid,), "one")
    if not row:
        return jsonify(error="This SOS is already closed."), 404
    responders = json.loads(row["responders"] or "[]")
    if row["uid"] != u["uid"] and not any(r["uid"] == u["uid"] for r in responders):
        return jsonify(error="Only the sender or a responder can close an SOS."), 403
    run("UPDATE sos SET resolved=1 WHERE id=?", (sid,), "none")
    row = run("SELECT * FROM sos WHERE id=?", (sid,), "one")
    broadcast("sos_update", sos_json(row))
    system_message(f"SOS from {row['name']} was closed by {u['name']}.", "general")
    return jsonify(sos=sos_json(row))


# --------------------------------------------------------------------------
# Network monitor
# --------------------------------------------------------------------------
def _read(path):
    with open(path) as fh:
        return fh.read()


def system_stats():
    s = {"temp_c": None, "load": None, "mem_total": None, "mem_used": None,
         "disk_total": None, "disk_used": None, "uptime": None, "cpus": os.cpu_count()}
    try:
        s["temp_c"] = round(int(_read("/sys/class/thermal/thermal_zone0/temp")) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        s["load"] = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        pass
    try:
        info = {}
        for line in _read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0]) * 1024
        s["mem_total"] = info["MemTotal"]
        s["mem_used"] = info["MemTotal"] - info.get("MemAvailable", info.get("MemFree", 0))
    except (OSError, ValueError, KeyError):
        pass
    try:
        du = shutil.disk_usage(DATA)
        s["disk_total"], s["disk_used"] = du.total, du.used
    except OSError:
        pass
    try:
        s["uptime"] = int(float(_read("/proc/uptime").split()[0]))
    except (OSError, ValueError):
        pass
    return s


def _ip_key(ip):
    try:
        return tuple(int(p) for p in ip.split("."))
    except ValueError:
        return (999,)


def read_devices():
    devices = {}
    try:
        for line in _read("/var/lib/misc/dnsmasq.leases").splitlines():
            p = line.split()
            if len(p) >= 4:
                devices[p[2]] = {"ip": p[2], "mac": p[1], "host": "" if p[3] == "*" else p[3]}
    except OSError:
        pass
    try:
        for line in _read("/proc/net/arp").splitlines()[1:]:
            p = line.split()
            if len(p) >= 6 and p[2] != "0x0":
                d = devices.setdefault(p[0], {"ip": p[0], "mac": p[3], "host": ""})
                d["mac"] = p[3]
    except OSError:
        pass
    return devices


@app.get("/api/network")
@auth
def network(u):
    with clients_lock:
        online = {c["uid"] for c in clients.values()}
    users = run("SELECT uid,name,status,ip,last_seen FROM users ORDER BY last_seen DESC LIMIT 200")
    for x in users:
        x["online"] = x["uid"] in online
    devices = {} if PUBLIC else read_devices()
    for x in users:
        if PUBLIC:
            x["ip"] = ""
        elif x["online"] and x["ip"] and x["ip"] not in devices:
            devices[x["ip"]] = {"ip": x["ip"], "mac": "", "host": ""}
    names_by_ip = {}
    for x in sorted(users, key=lambda r: r["online"]):
        names_by_ip[x["ip"]] = x["name"]
    out = []
    for d in sorted(devices.values(), key=lambda d: _ip_key(d["ip"])):
        d["user"] = names_by_ip.get(d["ip"], "")
        out.append(d)
    return jsonify(users=users, devices=out, system=system_stats(), hub_ip=HUB_IP, now=now())


# --------------------------------------------------------------------------
# Live stream
# --------------------------------------------------------------------------
@app.get("/api/stream")
@auth
def stream(u):
    cid = uuid.uuid4().hex
    q = queue.Queue(maxsize=300)
    with clients_lock:
        clients[cid] = {"q": q, "uid": u["uid"]}
    broadcast("presence", presence())

    def gen():
        try:
            yield "retry: 3000\n\n"
            yield f"event: hello\ndata: {json.dumps(presence())}\n\n"
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with clients_lock:
                clients.pop(cid, None)
            broadcast("presence", presence())

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------
# Start-up
# --------------------------------------------------------------------------
def ensure_cert():
    """Create a self-signed certificate so browsers allow the microphone (HTTPS)."""
    cert, key = DATA / "cert.pem", DATA / "key.pem"
    if cert.exists() and key.exists():
        return str(cert), str(key)
    print("Creating a self-signed certificate (one time)...")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "3650", "-subj", "/CN=depot.local",
         "-addext", f"subjectAltName=IP:{HUB_IP},IP:127.0.0.1,DNS:depot.local,DNS:localhost"],
        check=True, capture_output=True,
    )
    return str(cert), str(key)


def serve(host, port, ssl_context=None):
    from werkzeug.serving import make_server
    make_server(host, port, app, threaded=True, ssl_context=ssl_context).serve_forever()


def main():
    parser = argparse.ArgumentParser(description="DEPOT offline hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)),
                        help="HTTP port (80 on the Pi; hosts set PORT for you)")
    parser.add_argument("--https-port", type=int, default=0, help="also serve HTTPS on this port (443 on the Pi)")
    args = parser.parse_args()

    init_db()
    if args.https_port:
        try:
            ctx = ensure_cert()
            threading.Thread(target=serve, args=(args.host, args.https_port, ctx), daemon=True).start()
            print(f"HTTPS  https://{HUB_IP if args.host == '0.0.0.0' else args.host}:{args.https_port}")
        except (OSError, subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"HTTPS disabled ({exc}). Voice notes will use the phone's own recorder.", file=sys.stderr)
    print(f"HTTP   http://localhost:{args.port}   (data in {DATA})")
    try:
        serve(args.host, args.port)
    except KeyboardInterrupt:
        pass


# Create the database when a production server (gunicorn) imports this module.
init_db()

if __name__ == "__main__":
    main()
