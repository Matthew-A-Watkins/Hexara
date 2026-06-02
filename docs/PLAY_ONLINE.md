# Playing HEXARA with friends over the internet

Your friends are **not** on your local network, so they need a public address
that reaches the game running on your PC. Here are the realistic options,
cheapest-and-simplest first.

## TL;DR — the recommended way (free, one command)

```
py -3 share.py
```

That's it. This:
1. starts the game on your PC (localhost only), and
2. opens a free **Cloudflare Quick Tunnel**, printing a public link like
   `https://three-random-words.trycloudflare.com`.

Send that link to your friends — they just open it in a browser, type a name,
and enter your room code. When you're done, press **Ctrl+C** to close it.

- **Cost:** $0. No account, no sign-up.
- **Your steps:** one command.
- **Their steps:** open a link.
- **Safety:** the connection is **HTTPS (encrypted)**; only the game is exposed
  (not your computer or network); the link is a random, unguessable address.
- **Catch:** your PC must stay on and running `share.py` while you play (you're
  playing anyway), and you get a new link each time you start it.

The first run downloads Cloudflare's official `cloudflared` helper (~50 MB) into
`./bin` — nothing is installed system-wide. (Delete `./bin` anytime to reclaim
the space; it'll re-download next time.)

### Want it invite-only?
Set an access code before launching, and share it with friends along with the link:

```powershell
# PowerShell
$env:HEXARA_PASSWORD = "ourgamenight"
py -3 share.py
```
```bash
# macOS/Linux
HEXARA_PASSWORD=ourgamenight py -3 share.py
```
The join screen will then require that code. (Without it, your room code already
keeps strangers out of *your* game — the access code just gates the whole server.)

## The options compared

| Option | Cost | Your effort | Friends' effort | Always-on? | Safe? |
|---|---|---|---|---|---|
| **Cloudflare Quick Tunnel** (`share.py`) ✅ recommended | Free | 1 command | open a link | only while you run it | HTTPS, only the game exposed |
| ngrok | Free tier | install + account + authtoken | open a link (+ click through a warning page) | while you run it | HTTPS |
| Tailscale Funnel | Free | install + login + enable funnel | open a link | while you run it | HTTPS |
| Rent a tiny cloud server (Fly.io/Render/a VPS) | ~Free–$5/mo | create account, deploy | open a link | yes (24/7) | HTTPS |
| Router **port forwarding** | Free | router config | open a link | yes | ❌ not recommended |

**Why not port forwarding?** It exposes a port on your home router directly to
the whole internet with your real IP and no encryption, and it's fiddly to set
up. A tunnel is safer (encrypted, only the app is exposed, nothing opened on
your router) and easier.

**When to use a cloud server instead of a tunnel:** only if you want the game
reachable 24/7 without your PC being on. That needs an account and a deploy step,
so it's more work and possibly a small cost — overkill for a game night.

## How it works / why it's safe

- The game server binds to `127.0.0.1` (your machine only). `cloudflared` makes
  an **outbound** connection to Cloudflare and forwards just that one local port.
  Nothing is opened on your router, and your machine isn't otherwise exposed.
- All traffic between your friends and Cloudflare is **HTTPS** (TLS). The hop
  from Cloudflare to your PC rides the encrypted tunnel.
- The server is hardened for public exposure: request-size limits, a cap on the
  number of rooms, and automatic cleanup of abandoned games (so memory can't
  grow unbounded), plus the optional access code above.
- Worst case without an access code: a stranger who somehow guessed your random
  link lands on the join screen — they still can't enter *your* game without the
  4-letter room code you share privately.

## Troubleshooting

- **"cloudflared download failed"** — install it once manually and re-run:
  `winget install Cloudflare.cloudflared` (Windows) or `brew install cloudflared`
  (macOS), then `py -3 share.py`.
- **The link doesn't open for a friend** — make sure `share.py` is still running
  (the window open) and you sent the full `https://…trycloudflare.com` link. A
  brand-new link can take a few seconds to become reachable.
- **Port already in use** — `py -3 share.py 8123` to pick another local port.
- **You changed the code** — restart `share.py` to pick it up (you'll get a new link).
