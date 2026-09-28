#!/usr/bin/env python3
"""stack-status: tiny read-only status endpoint for the start page (~/dotfiles/startpage).

Browsers can only probe HTTP, so game servers are checked here instead:
  - Project Zomboid: Steam A2S_INFO query on its UDP game port
  - Minecraft: server list ping (players / max / version)
GET /  ->  JSON  {"vaultwarden": {...}, "pz": {...}, "minecraft": {...}}   (CORS open, LAN only)
Also HTTP services whose CORP header stops browsers from probing them (Vaultwarden).

No Docker socket, no credentials, nothing it can change: it only knocks on ports.
Runs in a python:3.13-alpine container with host networking on the Unraid box.
"""
import json
import socket
import struct
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "192.168.0.10"
GAMES = {
    "pz": {"kind": "a2s", "port": 16261},
    "minecraft": {"kind": "mc", "port": 25565},
}
# HTTP services the browser cannot probe (they send cross-origin-resource-policy: same-origin)
SERVICES = {
    "vaultwarden": "http://127.0.0.1:8097/alive",
}
TIMEOUT = 2.0
LISTEN = ("0.0.0.0", 8091)


def a2s_info(host, port):
    """Steam A2S_INFO. refused = container stopped; timeout = running but not answering yet."""
    req = b"\xff\xff\xff\xffTSource Engine Query\x00"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((host, port))
        s.send(req)
        data = s.recv(4096)
        if data[4:5] == b"A":                      # challenge: resend with it
            s.send(req + data[5:9])
            data = s.recv(4096)
        if data[4:5] != b"I":
            return {"state": "online"}
        p = 6
        fields = []
        for _ in range(4):                          # name, map, folder, game
            end = data.index(b"\x00", p)
            fields.append(data[p:end].decode(errors="replace"))
            p = end + 1
        players, maxp = data[p + 2], data[p + 3]
        return {"state": "online", "name": fields[0], "players": players, "max": maxp}
    except ConnectionRefusedError:
        return {"state": "offline"}
    except socket.timeout:
        return {"state": "starting"}
    except OSError:
        return {"state": "offline"}
    finally:
        s.close()


def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _read_varint(sock):
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise OSError("closed")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7


def mc_ping(host, port):
    """Minecraft server list ping (1.7+)."""
    try:
        s = socket.create_connection((host, port), timeout=TIMEOUT)
    except ConnectionRefusedError:
        return {"state": "offline"}
    except OSError:
        return {"state": "offline"}
    try:
        s.settimeout(TIMEOUT)
        hs = _varint(0) + _varint(760) + _varint(len(host)) + host.encode() + struct.pack(">H", port) + _varint(1)
        s.sendall(_varint(len(hs)) + hs + b"\x01\x00")
        _read_varint(s)                             # packet length
        _read_varint(s)                             # packet id
        length = _read_varint(s)
        raw = b""
        while len(raw) < length:
            chunk = s.recv(length - len(raw))
            if not chunk:
                break
            raw += chunk
        info = json.loads(raw.decode(errors="replace"))
        motd = info.get("description", "")
        if isinstance(motd, dict):
            motd = motd.get("text", "")
        return {"state": "online", "name": motd, "players": info.get("players", {}).get("online"),
                "max": info.get("players", {}).get("max"), "version": info.get("version", {}).get("name")}
    except (OSError, ValueError):
        return {"state": "starting"}               # port open (Crafty/container up), server not answering
    finally:
        s.close()


def http_check(url):
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return {"state": "online" if r.status < 500 else "offline"}
    except Exception:
        return {"state": "offline"}


def status():
    out = {key: http_check(url) for key, url in SERVICES.items()}
    for key, g in GAMES.items():
        out[key] = a2s_info(HOST, g["port"]) if g["kind"] == "a2s" else mc_ping(HOST, g["port"])
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(status()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                    # keep the container log quiet
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()
