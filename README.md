# DEPOT hub: offline chat, files, voice notes and SOS

A Raspberry Pi becomes a Wi-Fi network and web server. Phones join it and use
the hub in a normal browser, with no internet, no app and no accounts.

## What is in the box
- Chat channels (General, Medical, Supplies, Rescue) with instant delivery
- File and photo sharing (25 MB each), plus a searchable Files library
- Voice notes recorded in the browser
- SOS: hold the button for 1 second; every phone gets a full-screen alarm with the sender's health card; responders tap "I'm coming"
- Safety check-in: safe / need help / injured
- Network view: people, Wi-Fi devices, Pi temperature, load, memory, storage
- Offline first-aid and disaster guide
- Spam guard with escalating timeouts (SOS is never blocked)

## Try it on a laptop (any OS)
    pip install -r requirements.txt
    python server.py
Open http://localhost:8080 in two browser windows with different names.
Voice recording works on localhost. For other devices use HTTPS (below).

## Run on the Pi
1. Copy this folder to the Pi and `cd` into it.
2. `sudo bash pi/setup.sh`
3. Join Wi-Fi `depot` (password `depot321`) and open `https://10.42.0.1`, accepting the one-time certificate warning. HTTPS is what lets browsers use the microphone.

Change the Wi-Fi password in `pi/hostapd.conf` and the country code before real use.

## Run on the internet
See `DEPLOY.md` for a step-by-step Render guide. Short version: the `Dockerfile` runs
`server.py` under gunicorn (one worker, many threads); set `DEPOT_PIN` to require a room PIN,
keep `DEPOT_DATA` on a persistent disk. Environment variables: `DEPOT_PIN`, `DEPOT_PUBLIC=1`,
`DEPOT_PROXY=1`, `DEPOT_MAX_STORAGE_MB`, `DEPOT_RATE_LIMIT=0`, `PORT`.

## Layout
    server.py         Flask API, live push (SSE), SQLite, spam guard
    web/index.html    the whole phone UI
    pi/               hostapd, dnsmasq, systemd units, setup script
    data/             created at runtime: depot.db, uploads/, certificate

## Notes and limits
- One Pi is the whole network. Everyone must be in its Wi-Fi range.
- Not end-to-end encrypted; the hub operator can read everything.
- Names are not verified. Downloads need a joined device (token or cookie), so a bare link no longer works for strangers.
- SOS alerts people on this Wi-Fi only. It does not contact emergency services.
- The Pi has no battery clock. The hub learns the time from the first phone that joins.
