"""Play HEXARA with friends over the internet — one command, no accounts.

    py -3 share.py

This starts the game server on your machine (localhost only) and opens a free
Cloudflare "Quick Tunnel", which gives you a public HTTPS link like
    https://three-random-words.trycloudflare.com
Share that link with your friends — they just open it in a browser. No router
setup, no port forwarding, traffic is encrypted (HTTPS), and only the game is
exposed (not your computer). The link lasts until you stop this command
(Ctrl+C); a new link is created each time you run it.

The first run downloads Cloudflare's official `cloudflared` helper (~15-40 MB)
into ./bin. Nothing is installed system-wide.

Options:
    py -3 share.py 8123            # use a different local port
    HEXARA_PASSWORD=secret py -3 share.py   # require an access code to join
      (PowerShell:  $env:HEXARA_PASSWORD="secret"; py -3 share.py)
"""

import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
BIN_DIR = os.path.join(ROOT, "bin")

TRYCF_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")


# ----------------------------------------------------------- cloudflared setup
def _asset_name():
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sysname == "windows":
        return "cloudflared-windows-%s.exe" % ("arm64" if arm else "amd64"), "cloudflared.exe"
    if sysname == "linux":
        return "cloudflared-linux-%s" % ("arm64" if arm else "amd64"), "cloudflared"
    if sysname == "darwin":
        return None, "cloudflared"  # macOS ships a .tgz; prefer brew (handled below)
    return None, "cloudflared"


def _which(name):
    from shutil import which
    return which(name)


def find_cloudflared():
    """Return a path to a usable cloudflared, or None."""
    onpath = _which("cloudflared")
    if onpath:
        return onpath
    _, local_name = _asset_name()
    local = os.path.join(BIN_DIR, local_name)
    if os.path.isfile(local):
        return local
    return None


def download_cloudflared():
    asset, local_name = _asset_name()
    if asset is None:
        print("\nCould not auto-download cloudflared for this OS.")
        print("Install it once with your package manager, e.g.:")
        print("  macOS:  brew install cloudflared")
        print("  or grab it from https://github.com/cloudflare/cloudflared/releases/latest")
        return None
    os.makedirs(BIN_DIR, exist_ok=True)
    dest = os.path.join(BIN_DIR, local_name)
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/" + asset
    print("First run: downloading Cloudflare's official tunnel helper…")
    print("  from " + url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hexara-share"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            read = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if total:
                    pct = read * 100 // total
                    sys.stdout.write("\r  %d%% (%d KB)" % (pct, read // 1024))
                    sys.stdout.flush()
        print("\n  done -> " + dest)
        if platform.system().lower() != "windows":
            os.chmod(dest, 0o755)
        return dest
    except Exception as e:  # noqa: BLE001
        print("\nDownload failed: %s" % e)
        print("Install cloudflared manually from "
              "https://github.com/cloudflare/cloudflared/releases/latest")
        try:
            if os.path.isfile(dest):
                os.remove(dest)
        except OSError:
            pass
        return None


# ------------------------------------------------------------------- server
def start_server(port):
    from server.app import make_server  # imported here so deps resolve via sys.path
    server = make_server("127.0.0.1", port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ------------------------------------------------------------------- tunnel
def run_tunnel(cloudflared, port):
    proc = subprocess.Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    public_url = {"v": None}

    def reader():
        for line in proc.stdout:
            if public_url["v"] is None:
                m = TRYCF_RE.search(line)
                if m:
                    public_url["v"] = m.group(0)
                    _banner(public_url["v"])
            # else: stay quiet; uncomment to debug:  print("[cloudflared]", line.rstrip())

    threading.Thread(target=reader, daemon=True).start()
    return proc, public_url


def _banner(url):
    line = "=" * 64
    print("\n" + line)
    print("  HEXARA is live on the internet!  Share this link with friends:")
    print("")
    print("      " + url)
    print("")
    pw = (os.environ.get("HEXARA_PASSWORD") or "").strip()
    if pw:
        print("  Access code required to join (you set HEXARA_PASSWORD).")
    print("  Keep this window open while you play. Press Ctrl+C to stop.")
    print(line + "\n")


def main():
    # Flush prints promptly even when output is piped/redirected, so the
    # shareable link shows up as soon as the tunnel is ready.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    port = 8000
    for a in sys.argv[1:]:
        if a.isdigit():
            port = int(a)
    if os.environ.get("HEXARA_PORT", "").isdigit():
        port = int(os.environ["HEXARA_PORT"])

    cf = find_cloudflared()
    if not cf:
        cf = download_cloudflared()
    if not cf:
        sys.exit(1)

    try:
        start_server(port)
    except OSError as e:
        print("Could not start the game server on port %d: %s" % (port, e))
        print("Another program may be using that port. Try:  py -3 share.py 8123")
        sys.exit(1)
    print("Game server running locally on http://127.0.0.1:%d" % port)
    print("Opening a public tunnel (this can take a few seconds)…")

    proc, public_url = run_tunnel(cf, port)

    # Wait up to ~30s for the public URL, then keep running.
    for _ in range(60):
        if public_url["v"] or proc.poll() is not None:
            break
        time.sleep(0.5)
    if not public_url["v"]:
        print("\nThe tunnel didn't report a public URL.")
        print("Check your internet connection and try again.")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping the tunnel and server…")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
