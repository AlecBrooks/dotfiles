#!/usr/bin/env python3
"""workshop HUD: the workshop theme's desk. A drafting sheet drawn on the background layer of each
monitor, with live readouts in panels. Every number on it is real and every panel names its source.

  hud.py on|off [MONITOR|all]   start/stop it (default: all monitors)
  hud.py status                 which monitors have it, and when each last drew
  hud.py run MONITOR            the sheet window itself (what "on" starts in the background)
  hud.py data MONITOR           print one round of that sheet's data as JSON (for testing)
  hud.py snapshot MONITOR PNG   save what the running sheet on MONITOR shows, windows or not
  hud.py stacks MONITOR         dump the running sheet's Python stacks into the log (if it hangs)

The main monitor (DP-3, else the first one) gets sheet 1, SYSTEM: CPU, memory, GPU, storage,
processes, network, Tailscale links, the home server and Jarvis. Every other monitor gets sheet 2,
DAY: a 24-hour dial with today's calendar on it, the journal, active projects, what's playing and
the weather.

The page (sheet.html) only draws; this script collects the numbers and writes them as JSON files in
$XDG_RUNTIME_DIR/workshop-hud/<MONITOR>/, which the page reads by itself: quick readings every second,
slow ones (ssh, tailscale, the vault...) on their own thread so they never hold the rest up, and Jarvis
every 0.1 s so the ring follows him. Nothing calls into the page from here: evaluate_javascript made
the GTK process host JavaScript values, and its garbage collector deadlocked the main loop (both
sheets froze for good, 2026-10-27). Clicks fall through to the desktop. Started and stopped by the
theme's hook.

Optional: WORKSHOP_SERVER (ssh host for the SERVER panel, default "unraid"), OBSIDIAN_VAULT
(default ~/GitHub/Obsidian).
"""
import faulthandler
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
HOME = os.path.expanduser("~")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
MAIN_MONITOR = "DP-3"
JARVIS = os.path.join(HOME, ".local/share/jarvis")
SERVER = os.environ.get("WORKSHOP_SERVER", "unraid")
VAULT = os.path.expanduser(os.environ.get("OBSIDIAN_VAULT", "~/GitHub/Obsidian"))
TICK = 1.0   # seconds between quick readings


def sh(*cmd, timeout=8):
    """A command's stdout ('' if it's missing or fails): every source is optional."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def monitors():
    try:
        return json.loads(sh("hyprctl", "monitors", "-j", timeout=5) or "[]")
    except ValueError:
        return []


def sheet_for(name):
    names = [m["name"] for m in monitors()]
    main = MAIN_MONITOR if MAIN_MONITOR in names else (names[0] if names else name)
    return "system" if name == main else "day"


class Slow:
    """Readings that take a while (network calls, ssh): each refreshed on its own interval on one
    background thread; the sheet gets whatever was read last."""

    def __init__(self, jobs):
        self.jobs = jobs                      # name -> (interval seconds, function)
        self.values, self.due = {}, {k: 0 for k in jobs}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            now = time.monotonic()
            for name, (every, fn) in self.jobs.items():
                if now >= self.due[name]:
                    try:
                        self.values[name] = fn()
                    except Exception as exc:
                        self.values[name] = None
                        print(f"hud: {name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                    self.due[name] = time.monotonic() + every
            time.sleep(0.5)

    def get(self):
        return dict(self.values)


# ---------------------------------------------------------------- sheet 1: SYSTEM

def hwmon(name):
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if read(os.path.join(d, "name")) == name:
            return d
    return None


class System:
    def __init__(self):
        self.prev_cpu = None
        self.prev_net = None
        self.cpu_hist = []
        self.net_hist = []
        self.k10 = hwmon("k10temp") or hwmon("coretemp")
        self.gpu = hwmon("amdgpu")
        try:
            import psutil
            self.psutil = psutil
            for p in psutil.process_iter():   # first cpu_percent() call of each process is 0: prime them
                try:
                    p.cpu_percent()
                except psutil.Error:
                    pass
        except ImportError:
            self.psutil = None
        self.slow = Slow({"links": (20, links), "storage": (30, storage), "server": (15, server)})

    def cpu(self):
        rows = [l.split() for l in read("/proc/stat").splitlines() if l.startswith("cpu")]
        now = [(sum(map(int, r[1:])), int(r[4]) + int(r[5])) for r in rows]   # (total, idle+iowait)
        usage = []
        if self.prev_cpu and len(self.prev_cpu) == len(now):
            for (t0, i0), (t1, i1) in zip(self.prev_cpu, now):
                dt = t1 - t0
                usage.append(round(100 * (1 - (i1 - i0) / dt), 1) if dt else 0.0)
        self.prev_cpu = now
        total = usage[0] if usage else 0.0
        self.cpu_hist = (self.cpu_hist + [total])[-180:]
        info = read("/proc/cpuinfo")
        mhz = [float(m) for m in re.findall(r"cpu MHz\s*:\s*([\d.]+)", info)]
        temp = read(os.path.join(self.k10, "temp1_input")) if self.k10 else ""
        model = re.search(r"model name\s*:\s*(.+)", info)
        return {
            "usage": total, "threads": usage[1:], "history": self.cpu_hist,
            "temp": int(temp) / 1000 if temp else None,
            "mhz": round(sum(mhz) / len(mhz)) if mhz else None,
            "load": read("/proc/loadavg").split()[:3],
            "model": model.group(1).replace("6-Core Processor", "").strip() if model else "",
        }

    def memory(self):
        m = {}
        for line in read("/proc/meminfo").splitlines():
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0]) * 1024
        total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
        return {"total": total, "used": total - avail, "cached": m.get("Cached", 0),
                "swap_total": m.get("SwapTotal", 0), "swap_used": m.get("SwapTotal", 0) - m.get("SwapFree", 0)}

    def gpu_info(self):
        if not self.gpu:
            return None
        dev = os.path.realpath(os.path.join(self.gpu, "device"))
        g = lambda f: read(os.path.join(self.gpu, f))
        d = lambda f: read(os.path.join(dev, f))
        num = lambda s, div=1: (int(s) / div) if s.lstrip("-").isdigit() else None
        return {
            "busy": num(d("gpu_busy_percent")),
            "edge": num(g("temp1_input"), 1000), "junction": num(g("temp2_input"), 1000), "mem": num(g("temp3_input"), 1000),
            "junction_crit": num(g("temp2_crit"), 1000),
            "power": num(g("power1_average") or g("power1_input"), 1e6), "power_cap": num(g("power1_cap"), 1e6),
            "fan": num(g("fan1_input")),
            "vram_used": num(d("mem_info_vram_used")), "vram_total": num(d("mem_info_vram_total")),
            "name": "RX 6650 XT" if "73ef" in d("device") else "GPU",
        }

    def network(self):
        rx = tx = 0
        for line in read("/proc/net/dev").splitlines()[2:]:
            name, rest = line.split(":", 1)
            name = name.strip()
            if name == "lo" or name.startswith(("tailscale", "docker", "veth", "virbr", "br-")):
                continue
            f = rest.split()
            rx += int(f[0]); tx += int(f[8])
        now = time.monotonic()
        down = up = 0.0
        if self.prev_net:
            t0, rx0, tx0 = self.prev_net
            dt = max(now - t0, 0.001)
            down, up = (rx - rx0) / dt, (tx - tx0) / dt
        self.prev_net = (now, rx, tx)
        self.net_hist = (self.net_hist + [[down, up]])[-180:]
        route = sh("ip", "-4", "route", "get", "1.1.1.1", timeout=3)
        dev = re.search(r"\bdev (\S+)", route)
        src = re.search(r"\bsrc (\S+)", route)
        return {"down": down, "up": up, "history": self.net_hist, "rx_total": rx, "tx_total": tx,
                "iface": dev.group(1) if dev else "", "ip": src.group(1) if src else ""}

    def processes(self):
        if not self.psutil:
            return []
        rows = []
        for p in self.psutil.process_iter(["name", "memory_info"]):
            try:
                cpu = p.cpu_percent()
            except self.psutil.Error:
                continue
            mem = p.info["memory_info"].rss if p.info.get("memory_info") else 0
            rows.append({"name": p.info["name"] or "?", "cpu": cpu, "mem": mem})
        rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=True)
        return rows[:6]

    def collect(self):
        return {"cpu": self.cpu(), "memory": self.memory(), "gpu": self.gpu_info(), "net": self.network(),
                "procs": self.processes(), "uptime": float(read("/proc/uptime", "0").split()[0]),
                "kernel": os.uname().release, **self.slow.get()}


def links():
    try:
        d = json.loads(sh("tailscale", "status", "--json", timeout=6) or "{}")
    except ValueError:
        return None
    if not d.get("Self"):
        return None
    peers = []
    for p in d.get("Peer", {}).values():
        if p.get("HostName") == "localhost":
            continue
        peers.append({"name": p.get("HostName", "?"), "online": bool(p.get("Online")), "exit": bool(p.get("ExitNode"))})
    peers.sort(key=lambda p: (not p["online"], p["name"].lower()))
    return {"peers": peers, "exit": next((p["name"] for p in peers if p["exit"]), None)}


def storage():
    out, seen = [], set()
    for line in read("/proc/mounts").splitlines():
        dev, mnt, fs = line.split()[:3]
        if not dev.startswith("/dev/") or fs in ("squashfs", "vfat", "iso9660") or mnt.startswith(("/boot", "/efi")) or dev in seen:
            continue
        seen.add(dev)
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        out.append({"mount": mnt, "fs": fs, "dev": os.path.basename(dev), "total": total,
                    "used": total - st.f_bavail * st.f_frsize})
    return out[:4]


# One ssh round trip; each section starts with "@name". Only reads, nothing is changed on the server.
SERVER_SCRIPT = r"""
echo @uptime; cat /proc/uptime
echo @load; cat /proc/loadavg
echo @mem; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
echo @array; grep -E '^(mdState|mdNumDisks|mdResyncPos|mdResyncSize|sbSynced)=' /var/local/emhttp/var.ini 2>/dev/null
echo @shares; df -B1 --output=size,used /mnt/user 2>/dev/null | tail -n1
echo @temp; sensors 2>/dev/null | grep -m1 -E '^(Package id 0|Tctl|Tdie):'
echo @containers; docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}' 2>/dev/null
"""


def server():
    out = sh("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", SERVER, SERVER_SCRIPT, timeout=12)
    if "@uptime" not in out:
        return {"host": SERVER, "reachable": False}
    sec, cur = {}, None
    for line in out.splitlines():
        if line.startswith("@"):
            cur = line[1:].strip()
            sec[cur] = []
        elif cur:
            sec[cur].append(line)
    first = lambda k: (sec.get(k) or [""])[0]
    mem = dict(re.findall(r"^(\w+):\s+(\d+)", "\n".join(sec.get("mem", [])), re.M))
    arr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?', "\n".join(sec.get("array", [])), re.M))
    shares = first("shares").split()
    temp = re.search(r"\+([\d.]+)", first("temp"))
    containers = []
    for line in sec.get("containers", []):
        parts = line.split("|", 2)
        if len(parts) == 3:
            name, state, status = parts
            containers.append({"name": name, "up": state == "running",
                               "health": "unhealthy" if "unhealthy" in status else "healthy" if "healthy" in status else ""})
    containers.sort(key=lambda c: (not c["up"], c["name"].lower()))
    resync = int(arr.get("mdResyncPos") or 0)
    return {
        "host": SERVER, "reachable": True,
        "uptime": float(first("uptime").split()[0]) if first("uptime") else None,
        "load": first("load").split()[:3],
        "mem_total": int(mem.get("MemTotal", 0)) * 1024, "mem_used": (int(mem.get("MemTotal", 0)) - int(mem.get("MemAvailable", 0))) * 1024,
        "array": arr.get("mdState", ""), "disks": arr.get("mdNumDisks", ""),
        "resync": round(100 * resync / int(arr["mdResyncSize"]), 1) if resync and arr.get("mdResyncSize") else None,
        "share_total": int(shares[0]) if len(shares) == 2 else None, "share_used": int(shares[1]) if len(shares) == 2 else None,
        "temp": float(temp.group(1)) if temp else None,
        "containers": containers,
    }


def jarvis():
    try:
        name, pid = read(os.path.join(JARVIS, "state")).split()
        os.kill(int(pid), 0)
        state = name
    except (ValueError, OSError):
        state = "off"
    said = []
    try:
        with open(os.path.join(JARVIS, "conversation.log"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in lines:
            m = re.match(r"^(You|Vox|Jarvis):\s*(.*)", line)   # Jarvis: lines from before the rename
            if m:
                said.append({"who": m.group(1), "text": m.group(2)})
    except OSError:
        pass
    # the last exchange: his last question and everything Vox said since (all of it, the HUD shows it whole)
    start = max((i for i, l in enumerate(said) if l["who"] == "You"), default=max(0, len(said) - 1))
    return {"state": state, "last": said[start:][-12:]}


# ---------------------------------------------------------------- sheet 2: DAY

def calendar():
    fmt = "%(start:%Y-%m-%d %H:%M)|%(end:%Y-%m-%d %H:%M)|%m\\n"
    out = sh("calcurse", "-Q", "--days", "2", "--format-apt", fmt, "--format-recur-apt", fmt,
             "--format-event", "EV|%m\\n", "--format-recur-event", "EV|%m\\n", timeout=8)
    items = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            items.append({"start": parts[0], "end": parts[1], "what": parts[2]})
    return items


def plain(md):
    """Markdown to one plain line: [[a|b]] -> b, [[a]] -> a, no emphasis/code marks."""
    md = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    return re.sub(r"\s+", " ", re.sub(r"[*_`]+", "", md)).strip()


def journal():
    """The newest journal entries' summaries (written every evening): what got done, day by day."""
    out = []
    for path in sorted(glob.glob(os.path.join(VAULT, "Journal", "????-??-??.md")), reverse=True)[:60]:
        text = read(path)
        m = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        body = plain(m.group(1)) if m else ""
        if body and not re.match(r"nothing (was )?worked on", body, re.I):
            out.append({"date": os.path.basename(path)[:-3], "text": body})
        if len(out) == 4:
            break
    return out


def projects():
    """Project notes by when they last changed: what's actually being worked on."""
    rows = []
    for path in glob.glob(os.path.join(VAULT, "Projects", "*.md")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        rows.append({"name": os.path.basename(path)[:-3], "mtime": mtime})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:7]


def script_json(name):
    try:
        return json.loads(sh(os.path.join(HOME, "bin", name), timeout=15) or "null")
    except ValueError:
        return None


class Day:
    def __init__(self):
        self.slow = Slow({"calendar": (60, calendar), "journal": (60, journal), "projects": (60, projects),
                          "weather": (900, lambda: script_json("waybar-weather"))})

    def media(self):
        out = sh("playerctl", "metadata", "--format",
                 "{{status}}\t{{playerName}}\t{{artist}}\t{{title}}\t{{position}}\t{{mpris:length}}", timeout=3)
        f = out.rstrip("\n").split("\t")
        if len(f) < 6 or f[0] not in ("Playing", "Paused"):
            return None
        num = lambda s: int(s) / 1e6 if s.isdigit() else None
        return {"status": f[0], "player": f[1], "artist": f[2], "title": f[3], "position": num(f[4]), "length": num(f[5])}

    def collect(self):
        return {"media": self.media(), **self.slow.get()}


# ---------------------------------------------------------------- the window

def pidfile(name):
    return os.path.join(RUNTIME, f"workshop-hud-{name}.pid")


def shotfile(name):
    return os.path.join(RUNTIME, f"workshop-hud-{name}.png")


def beatfile(name):
    return os.path.join(RUNTIME, f"workshop-hud-{name}.beat")


def datadir(name):
    return os.path.join(RUNTIME, "workshop-hud", name)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)   # the page never reads half a file


def running(name):
    try:
        pid = int(read(pidfile(name)))
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            if b"hud.py" in f.read():
                return pid
    except (OSError, ValueError):
        pass
    return None


def run(name):
    import cairo
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, WebKit2

    # `hud.py stacks`: Python stacks of every thread into the log, even with the main loop stuck.
    faulthandler.register(signal.SIGUSR2, file=sys.stderr, all_threads=True)

    mon = next((m for m in monitors() if m["name"] == name), None)
    if not mon:
        sys.exit(f"hud: no monitor {name}")
    display = Gdk.Display.get_default()
    gmon = None
    for i in range(display.get_n_monitors()):
        g = display.get_monitor(i).get_geometry()
        if (g.x, g.y) == (mon["x"], mon["y"]):
            gmon = display.get_monitor(i)
    if gmon is None:
        sys.exit(f"hud: GTK can't see {name}")

    window = Gtk.Window()
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "workshop-hud")
    # The background layer: under the bar and under the tank wallpaper (bottom layer), never over a window.
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BACKGROUND)
    GtkLayerShell.set_monitor(window, gmon)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)
    GtkLayerShell.set_exclusive_zone(window, -1)    # the whole screen, under the bar too (it's glass)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)

    view = WebKit2.WebView()
    view.set_background_color(Gdk.RGBA(0.059, 0.055, 0.047, 1))   # the desk's ink, no white flash
    settings = view.get_settings()
    settings.set_enable_write_console_messages_to_stdout(True)
    # The page reads its data files (file:// next to nothing else; it loads no remote content).
    settings.set_allow_file_access_from_file_urls(True)
    # Software drawing: a mostly static page doesn't need the GPU, and it's one less thing to go wrong
    # on a surface that's usually covered.
    settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
    window.add(view)
    window.connect("realize", lambda w: w.input_shape_combine_region(cairo.Region()))   # clicks fall through
    window.connect("destroy", Gtk.main_quit)

    sheet = sheet_for(name)
    names = [m["name"] for m in monitors()]
    meta = {
        "sheet": sheet, "monitor": name, "host": socket.gethostname(), "user": os.environ.get("USER", ""),
        "width": mon["width"], "height": mon["height"],
        "mm_w": mon.get("physicalWidth") or 0, "mm_h": mon.get("physicalHeight") or 0,
        "index": names.index(name) + 1 if name in names else 1, "count": len(names) or 1,
        "palette": palette(), "server": SERVER,
    }
    out = datadir(name)
    os.makedirs(out, exist_ok=True)
    write_json(os.path.join(out, "meta.json"), meta)
    collector = System() if sheet == "system" else Day()
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                data = collector.collect()
            except Exception as exc:   # one broken source must not take the desk down
                data = {"error": f"{type(exc).__name__}: {exc}"}
            data["now"] = time.time()
            try:
                write_json(os.path.join(out, "data.json"), data)
            except OSError:
                pass
            stop.wait(max(0.1, TICK - (time.monotonic() - t0)))

    def watch_jarvis():
        """Jarvis's state and voice, checked ten times a second; rewritten only when they change.
        jarvis.json carries the voice clip's mtime, so the page fetches voice.json only when it's new."""
        last = last_voice = None
        voice_path = os.path.join(JARVIS, "voice.json")
        while not stop.is_set():
            try:
                mtime = os.path.getmtime(voice_path)
            except OSError:
                mtime = None
            if mtime and mtime != last_voice:
                try:
                    shutil.copyfile(voice_path, os.path.join(out, "voice.json.tmp"))
                    os.replace(os.path.join(out, "voice.json.tmp"), os.path.join(out, "voice.json"))
                    last_voice = mtime
                except OSError:
                    pass
            j = {"jarvis": jarvis(), "voice": last_voice}
            if j != last:
                last = j
                try:
                    write_json(os.path.join(out, "jarvis.json"), j)
                except OSError:
                    pass
            stop.wait(0.1)

    def beat():   # proof the GTK main loop is alive (`hud.py status` shows its age)
        try:
            with open(beatfile(name), "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass
        return True

    view.load_uri("file://" + os.path.join(HERE, "sheet.html") + "?data=" + out)
    threading.Thread(target=worker, daemon=True).start()
    if sheet == "system":
        threading.Thread(target=watch_jarvis, daemon=True).start()
    GLib.timeout_add_seconds(1, beat)
    window.show_all()

    # SIGUSR1: save what the sheet shows (even under windows) to shotfile(name), for `hud.py snapshot`.
    def save(v, res):
        v.get_snapshot_finish(res).write_to_png(shotfile(name))
    def shoot():
        view.get_snapshot(WebKit2.SnapshotRegion.FULL_DOCUMENT, WebKit2.SnapshotOptions.NONE, None, save)
        return True
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, shoot)

    with open(pidfile(name), "w") as f:
        f.write(str(os.getpid()))
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: Gtk.main_quit() or False)
    try:
        Gtk.main()
    finally:
        stop.set()
        if running(name) == os.getpid():
            os.remove(pidfile(name))


def palette():
    """The theme's colors, from the generated palette (falls back to the page's built-in ones)."""
    try:
        c = json.load(open(os.path.join(HOME, ".cache/wallpaper-theme/.colors.json")))["colors"]
        return {k: c[k]["default"]["color"] for k in
                ("background", "surface", "surface_container", "outline", "outline_variant", "primary",
                 "yellow", "primary_container", "on_surface", "on_surface_variant", "error", "tertiary", "green")}
    except (OSError, ValueError, KeyError):
        return {}


def turn_on(name):
    if not running(name):
        log = open(os.path.join(RUNTIME, "workshop-hud.log"), "ab")
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "run", name], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)


def turn_off(name):
    pid = running(name)
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not running(name):
                return
            time.sleep(0.1)
        # A stuck main loop never handles SIGTERM: don't leave it holding the monitor.
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    try:
        os.remove(pidfile(name))
    except OSError:
        pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else "all"
    targets = [m["name"] for m in monitors()] if arg == "all" else [arg]
    if cmd == "off" and arg == "all":
        # also any sheet whose monitor is gone
        targets = sorted({os.path.basename(p)[len("workshop-hud-"):-4] for p in glob.glob(os.path.join(RUNTIME, "workshop-hud-*.pid"))} | set(targets))
    if cmd == "run":
        run(arg)
    elif cmd == "snapshot":
        pid = running(arg)
        if not pid:
            sys.exit(f"hud: no sheet running on {arg}")
        shot = shotfile(arg)
        if os.path.exists(shot):
            os.remove(shot)
        os.kill(pid, signal.SIGUSR1)
        for _ in range(50):
            if os.path.exists(shot):
                time.sleep(0.2)
                shutil.move(shot, sys.argv[3])
                break
            time.sleep(0.1)
        else:
            sys.exit("hud: the sheet didn't answer")
    elif cmd == "stacks":
        pid = running(arg)
        if pid:
            os.kill(pid, signal.SIGUSR2)
            print(f"stacks written to {os.path.join(RUNTIME, 'workshop-hud.log')}")
    elif cmd == "data":
        c = System() if sheet_for(arg) == "system" else Day()
        time.sleep(5)          # let the slow readings arrive
        c.collect()
        time.sleep(1)
        print(json.dumps(c.collect(), indent=1)[:8000])
    elif cmd in ("on", "off"):
        for t in targets:
            (turn_on if cmd == "on" else turn_off)(t)
    elif cmd == "status":
        for t in targets:
            beat = read(beatfile(t))
            age = f", last drew {time.time() - float(beat):.0f} s ago" if running(t) and beat else ""
            print(f"{t} {sheet_for(t)} {'on' if running(t) else 'off'}{age}")
    else:
        sys.exit(__doc__)
