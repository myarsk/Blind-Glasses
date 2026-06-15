"""
Report this device's network addresses to the configured Telegram chat.

Run on boot (via systemd) so you always know which IP to SSH into — even if the
main app fails to start. Deliberately uses only the standard library so it works
regardless of the state of the rest of the venv.

    python report_ip.py
"""

import getpass
import socket
import time
import urllib.parse
import urllib.request

import config as cfg_module

# Tried in order; first one that responds wins.
_PUBLIC_IP_SERVICES = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)

_NET_WAIT_TRIES = 30   # × 2s = up to 60s for the network to come up at boot


def _primary_local_ip():
    """The LAN IP of the interface used to reach the internet (no traffic sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _other_local_ips(skip):
    """Any additional non-loopback IPv4 addresses (e.g. a second interface)."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip != skip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def _public_ip(timeout=10):
    for url in _PUBLIC_IP_SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
                if ip:
                    return ip
        except Exception:
            continue
    return None


def _send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    with urllib.request.urlopen(url, data=data, timeout=15) as r:
        return 200 <= r.status < 300


def main():
    cfg = cfg_module.load()
    if not cfg.telegram_token or not cfg.partner_chat_id:
        print("[report_ip] Telegram not configured — run setup.py first.")
        return

    # Wait for the network to come up after a boot.
    local = None
    for _ in range(_NET_WAIT_TRIES):
        local = _primary_local_ip()
        if local:
            break
        time.sleep(2)

    public = _public_ip()
    hostname = socket.gethostname()
    try:
        user = getpass.getuser()
    except Exception:
        user = "pi"

    lines = [f"\U0001F50C {hostname} is online"]
    if local:
        lines.append("")
        lines.append("Local network — connect with:")
        lines.append(f"  ssh {user}@{local}")
        for ip in _other_local_ips(skip=local):
            lines.append(f"  ssh {user}@{ip}")
    if public:
        lines.append("")
        lines.append(f"Public IP: {public}  (needs a port-forward for SSH)")
    if not local and not public:
        lines.append("⚠️ No network address detected.")

    text = "\n".join(lines)
    try:
        _send_telegram(cfg.telegram_token, cfg.partner_chat_id, text)
        print("[report_ip] Sent:\n" + text)
    except Exception as e:
        print(f"[report_ip] Failed to send: {e}")


if __name__ == "__main__":
    main()
