# DEPOT Offline Communication Hub: handoff briefing

## 1. One-paragraph summary
DEPOT is a disaster-management communication hub. A Raspberry Pi acts as a Wi-Fi hotspot and web server; phones join it and use a browser UI to chat, share files, send voice notes, raise SOS alerts and watch the network, all with no internet. The owner's GitHub repo is `https://github.com/Rahulrao2506/depot-hub` (based on the idea of `tejdeepsingh18/DEPOT-Offline-Communication-Hub`). The original repo's source files were not visible to the previous AI, only its README, so the code here is a fresh implementation of what that README describes.

## 2. What has been built
| Path | Purpose |
|---|---|
| `server.py` | Flask backend (only dependency: flask). SQLite storage, file uploads on disk, live push via Server-Sent Events, spam guard, SOS, network monitor, captive-portal redirects, optional self-signed HTTPS. |
| `web/index.html` | The whole phone UI as one file (no CDNs, system fonts, works offline). Tabs: Chat, Files, Network, Guide, Me. Light and dark mode. |
| `pi/` | `hostapd.conf`, `dnsmasq-depot.conf`, three systemd units, `setup.sh` (turns a Raspberry Pi into the hotspot at 10.42.0.1 and starts the server on ports 80/443). |
| `site/index.html` | Static landing page for Vercel (hero, interactive phone mock, features, Pi setup steps, limits). |
| `site/app/index.html` | A copy of `web/index.html` with four small edits (below) so it runs on Vercel in demo mode. |
| `site/app/demo-backend.js` | In-browser fake server for demo mode. |
| `requirements.txt`, `README.md`, `.gitignore` | `flask>=2.2`; run instructions; ignores `data/`. |

### Features (all working and tested against a local server)
- Chat channels: general, medical, supplies, rescue. Live delivery, unread badges, reconnect and resync.
- File and photo sharing, 25 MB limit, Files tab with filter, search, download, delete (own files only). Images and video preview inline.
- Voice notes via MediaRecorder (needs HTTPS or localhost); fallback to the phone's own recorder through `<input type=file accept=audio/* capture>`.
- SOS: hold the header button for 1 second. Every phone gets a full-screen flashing alarm with sound and vibration, the sender's health card and address. Others tap "I'm coming"; sender sees responders; sender or responder can close it. SOS is never throttled.
- Safety check-in: safe / need help / injured, announced in General.
- Network tab: people (online, last seen, status), Wi-Fi devices (dnsmasq leases and ARP), Pi temperature, load, memory, storage, uptime.
- Offline guide: bleeding, CPR, burns, fracture, clean water, earthquake, flood.
- Spam guard: more than 6 messages in 8 s, 3 identical messages, or 15+ repeated characters gives timeouts of 10, 30, 60, 300, 600 s, with a countdown banner in the UI.
- Pi clock fix: the Pi has no battery clock, so the server learns a time offset from the first phone that joins if it differs by more than 120 s.
- Captive portal: `/generate_204`, `/hotspot-detect.html`, `/connecttest.txt` etc. redirect to `/`, and unknown paths redirect to `/` (dnsmasq resolves every name to the Pi).

### Backend API (all JSON; auth header `X-Depot-Token`; the SSE stream takes `?token=`)
`POST /api/join` `{name, health{blood,allergies,conditions,medications,contact,notes}, token?, client_time}` returns `{token,user,channels}` ·
`GET /api/me` · `POST /api/status {status}` · `GET /api/messages?channel=&after=` · `POST /api/messages {channel,text}` ·
`POST /api/upload` multipart `{file, channel, kind=file|voice, duration, text}` · `GET /uploads/<name>` · `GET /api/files` · `DELETE /api/files/<id>` ·
`GET /api/sos` · `POST /api/sos {note}` · `POST /api/sos/<id>/respond` · `POST /api/sos/<id>/resolve` · `GET /api/network` · `GET /api/stream` (SSE).
SSE events: `hello`, `presence`, `message`, `delete`, `people`, `sos`, `sos_update`.
429 responses carry `{error, retry_after}`.

### Design notes
Mobile-first, light and dark via CSS variables. Teal brand `#0B6E6E`, SOS red `#D92D20`. System font stack only, because the hub must work offline. The signature element is the hold-to-send SOS button with a fill sweep and the full-screen flashing alarm.

## 3. What happened in the conversation, in order
1. The owner asked for a website for the repo: offline file sharing on a Raspberry Pi, disaster management, Python backend plus a good UI. The AI read the repo README and built the Flask + SQLite + SSE backend and the single-page UI, then tested every endpoint on a laptop and syntax-checked the JavaScript. It also wrote the Pi hotspot configs, systemd units, `setup.sh`, README and a zip.
2. The owner opened it in VS Code on Windows. Problems solved:
   - `python` was the Microsoft Store alias. Fix: use `py server.py` (real Python is 3.14 under `AppData\Local\Python\pythoncore-3.14-64`). `pip install flask` had worked. The owner mistakenly ran `pip install python`, which does not exist.
   - `server.py` was not found because the unzip created a nested `depot-hub\depot-hub` folder. Fix: `cd` into the folder containing `server.py`.
   - The app then ran locally on `http://localhost:8080` and chat, file upload and SOS worked (seen in a screenshot).
3. The owner wanted a new GitHub repo and a Vercel deployment. Git steps were given. The push failed with "Repository not found" because the repo had not been created on GitHub under that exact name. After creating `Rahulrao2506/depot-hub` the push worked.
4. On Vercel, the import screen auto-picked the Flask preset on the repo root. The AI warned against deploying that (Vercel cannot run the always-on server, SQLite writes or SSE), and built a static showcase page `site/index.html`. Issues: the owner's `site/index.html` was empty until the content was pasted in; `site` only appears in Vercel's Root Directory list after it is pushed to GitHub. Final settings: Root Directory `site`, Application Preset `Other`. The showcase deployed successfully.
5. The owner said the actual local-hosted app, not a showcase, must be on Vercel. The AI built "demo mode" (`site/app/`): the real UI with `demo-backend.js` replacing the server inside the browser (fake `fetch`, `XMLHttpRequest` and `EventSource`; shared state in `localStorage`; tabs talk through `BroadcastChannel`; per-tab identity in `sessionStorage`; files stored as data URLs capped at 1.5 MB and served as blob URLs; simulated Pi stats). Tested with two Playwright tabs: messages, image sharing, SOS with health card, "I'm coming", network tab, spam banner all worked with no console errors. The landing page got an "Open the live app" button.
6. **Current problem:** the owner opened the demo on a laptop and a phone and got no cross-device messages. This is expected: demo mode shares data only between tabs of the same browser. Real cross-device chat on a public URL needs a real shared backend.
7. Options offered: (a) same Wi-Fi: run `py server.py` on the laptop and open `http://<laptop-ip>:8080` on the phone (works today); (b) host the real `server.py` on a platform that runs long-lived processes, e.g. Render, Railway or Fly (needs `--port` read from the `PORT` env var, persistent disk for `data/`); (c) rebuild for Vercel using serverless functions plus an external store (Upstash Redis for state, Vercel Blob for files) and replace SSE with short polling, because Vercel functions cannot hold long-lived connections or write to disk. The owner chose to hand the whole project to another AI.

### The four edits that turn `web/index.html` into `site/app/index.html`
1. Insert `<script src="demo-backend.js"></script>` immediately before the main `<script>`.
2. Add `<span class="demo-badge">DEMO</span>` inside the header wordmark, plus the `.demo-badge` CSS rule.
3. Add a "Demo mode" paragraph in the join card and a "stats are simulated" note in the Me tab.
4. Change the `<title>` to "DEPOT: live demo".

## 4. Known limitations
- Not end-to-end encrypted. Names are unverified. Upload URLs are unguessable but not authenticated.
- SOS only alerts people on the same network. It does not contact emergency services.
- Browsers only allow the microphone on HTTPS or localhost. The Pi setup serves a self-signed certificate on 443.
- Demo mode is single-browser only, with a 1.5 MB file cap and simulated system stats.
- The Pi setup scripts were written but never run on real hardware. Wi-Fi management differs across Pi OS versions (NetworkManager vs dhcpcd), so `pi/setup.sh` may need adjusting.

## 5. Prompt to give the other AI
Copy everything between the lines.

------------------------------------------------------------
You are a senior full-stack engineer. I am attaching a project called DEPOT, an offline communication hub for disaster management (chat, file sharing, voice notes, SOS alerts, safety check-ins, network monitor). It is a Flask + SQLite + Server-Sent Events backend (`server.py`) with a single-file mobile-first UI (`web/index.html`), Raspberry Pi hotspot configs in `pi/`, a static landing page in `site/index.html`, and a browser-only demo mode in `site/app/` (`demo-backend.js` fakes the API inside the browser). Read HANDOFF.md first, then the code.

Goal: make the real app work ACROSS DEVICES at a public URL, so that a laptop and a phone on different networks can chat, share files, send voice notes and raise SOS alerts to each other. Offline use on the Raspberry Pi must keep working with the same UI.

Do this:
1. Choose the best hosting approach and justify it briefly. Preferred: deploy the existing `server.py` to a host that supports long-running processes (Render, Railway or Fly.io): read the port from the `PORT` environment variable, keep data in a persistent volume (or move uploads to object storage and state to Postgres or Redis), add a `Procfile` or Dockerfile and a health check. If you believe Vercel is required, replace SSE with 1 to 2 second polling, move state to Upstash Redis and uploads to Vercel Blob, and implement the API as serverless functions with an identical JSON contract.
2. Keep the API contract and UI behaviour exactly as documented in HANDOFF.md section 2, so `web/index.html` needs minimal change.
3. Preserve the spam guard rules, SOS rules (never throttled, "I'm coming", close by sender or responder), the 25 MB upload limit, the clock-offset logic and the captive-portal redirects.
4. Fix the weak spots: add an admin-set room or PIN so strangers cannot read a public hub, add basic rate limits per IP, and add an upload size and type check.
5. Give me step-by-step deployment instructions for Windows, assuming I use VS Code, Git and GitHub (repo `Rahulrao2506/depot-hub`) and that I am a beginner. Tell me exactly which files to add, which commands to run and which settings to click.
6. Test the result with two separate clients and report what you verified.

Do not redesign the look of the UI. Keep it dependency-light, with no CDNs, so it still works offline on the Pi.
------------------------------------------------------------
