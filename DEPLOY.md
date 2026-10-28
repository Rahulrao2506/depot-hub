# Put DEPOT on the internet (Windows + VS Code + GitHub + Render)

After this, a laptop and a phone on different networks can chat, share files,
send voice notes and raise SOS alerts through one public https address, behind a PIN.
The Raspberry Pi setup is unchanged and still works offline.

## Which host, and why
DEPOT keeps live connections open (Server-Sent Events) and writes to a database and a
disk, so it needs a host that runs a normal always-on program. Vercel cannot do that,
so Vercel keeps the landing page and Render runs the real hub.

- **Render, paid Starter (recommended, about $7/month plus $0.25/GB for the disk):**
  always on, and chats and files survive restarts because of the persistent disk.
- **Render, Free instance ($0):** fine for a demo. It goes to sleep after about 15
  minutes without visitors (the first load then takes up to a minute) and it has no
  disk, so all chats and files are wiped whenever it restarts.

Prices change, so check render.com/pricing before you pay.

## Step 1. Add the new files to your project
In your `depot-hub` folder (the one that contains `server.py`) these must exist:

| File | Status |
|---|---|
| `server.py` | replaced (new version) |
| `web/index.html` | replaced (adds a PIN box) |
| `Dockerfile` | new |
| `.dockerignore` | new |
| `requirements-cloud.txt` | new |
| `render.yaml` | new |
| `fly.toml` | new (only for the optional Fly.io route) |
| `DEPLOY.md`, `README.md` | new / updated |

Easiest way: unzip `depot-hub-cloud.zip`, open the folder inside it, select everything and
paste it over your `depot-hub` folder, choosing "Replace". Do not leave a nested
`depot-hub\depot-hub` folder. In VS Code, `server.py` should sit directly under the folder you opened.

## Step 2. Try it on your own PC first (2 minutes)
Open the VS Code terminal (Terminal menu, New Terminal) and run:

    pip install flask
    $env:DEPOT_PIN="4821"
    py server.py

Open http://localhost:8080 in two browser windows. You should be asked for the PIN
(use 4821) and for a name. Stop the server with Ctrl+C.
Next time, run `Remove-Item Env:DEPOT_PIN` if you want to try it without a PIN.

## Step 3. Push to GitHub
In the VS Code terminal:

    git add .
    git commit -m "Cloud hosting: PIN, rate limits, upload checks, Dockerfile"
    git push

## Step 4. Create the service on Render
1. Go to https://render.com and choose **Sign up with GitHub**. Allow access to `Rahulrao2506/depot-hub`.
2. Click **New +** (top right), then **Blueprint**.
3. Pick the `depot-hub` repository and click **Connect**.
4. Render reads `render.yaml` and shows a service called `depot-hub`. It asks for **DEPOT_PIN**.
   Type the PIN you will share with your group (digits, at least 6 is better than 4).
5. Click **Apply** / **Deploy**. The first build takes about 3 to 6 minutes. Wait until the
   service says **Live**.
6. At the top of the service page you will see the address, like `https://depot-hub-xxxx.onrender.com`.

**Free demo instead:** skip the Blueprint. Click **New +**, **Web Service**, choose the repo,
set **Language** to `Docker`, **Instance Type** to `Free`, open **Advanced**, add environment
variables `DEPOT_PIN` (your PIN), `DEPOT_PUBLIC` = `1`, `DEPOT_PROXY` = `1`, set
**Health Check Path** to `/healthz`, and deploy. Do not add a disk (Free cannot have one).

## Step 5. Use it
Open the address on the laptop and on the phone (use mobile data on the phone to prove it
works across networks). Enter the PIN and a name on each. Send a message, a photo, a voice
note (https lets the microphone work), and hold the SOS button.

## Step 6. Link it from the Vercel landing page (optional)
In `site/index.html`, change the "Open the live app" link to your Render address, then
`git add . && git commit -m "link to live hub" && git push`. Vercel redeploys by itself.

## Settings you can change on Render (Environment tab)
| Variable | Meaning |
|---|---|
| `DEPOT_PIN` | Room PIN. Change it to lock out everyone who knew the old one (people already inside stay in until they leave). Remove it to make the hub open. |
| `DEPOT_MAX_STORAGE_MB` | Total file storage allowed (800 by default, disk is 1 GB). |
| `DEPOT_NAME` | Hub name returned by the config endpoint. |
| `DEPOT_RATE_LIMIT` | Set to `0` to switch the per-IP limits off. |

## If something goes wrong
- **Build fails:** open the service's **Logs** tab and read the last red lines. Most often a file from Step 1 is missing or is in a nested folder.
- **Page loads but "Cannot reach the hub":** the Free instance is waking up. Wait a minute and reload.
- **Everyone was logged out and old chats vanished:** the service restarted without a disk (Free), or the disk is not attached. Use the paid plan from the Blueprint.
- **"Too many wrong PINs":** that network is locked out for 5 minutes after 5 wrong tries.
- **Voice notes do nothing:** make sure the address starts with `https://`.
- **Changed the code later:** `git add . && git commit -m "update" && git push` and Render redeploys (autoDeploy is on).

## Limits you should know
- One server process only (live push and the spam guard are in memory). About 150 to 200 people online at once is the practical ceiling; more needs a redesign with Redis.
- The PIN is one shared secret. Anyone with it can read everything, and the host operator can read everything. Messages are not end-to-end encrypted.
- SOS reaches only people inside this hub. It does not contact emergency services.
- If the internet is down, a cloud hub is unreachable. Use the Raspberry Pi for that case.
