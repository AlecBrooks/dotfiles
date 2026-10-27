#!/usr/bin/env python3
"""workshop unfold: how windows open and close in the workshop theme.

Opening: a point appears where the window's center will be, a glowing line draws out from it both
ways to the window's width, then the line opens up and down into a frame of the window's size, and
the window appears inside it. Closing runs it backwards: the frame folds into a line, the line into
a point.

The theme's hypr apply() hides each new window (opacity 0) and shows it again after OPEN_MS; this
draws the rest on an overlay layer. It learns about windows from Hyprland's event socket, so
Hyprland never waits on it: if this isn't running, windows just appear after OPEN_MS.

  unfold.py run          the overlay (what the theme's hook starts)
  unfold.py on|off       start/stop it in the background
"""
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time

OPEN_MS = 700        # keep in step with UNFOLD_MS in templates/hypr-colors.lua
LINE_MS = 280        # of that, the line drawing out; the rest opens it vertically
FADE_MS = 220        # the frame fading off the finished window
CLOSE_MS = 460
FOLD_MS = 260        # of that, the frame folding into the line
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PIDFILE = os.path.join(RUNTIME, "workshop-unfold.pid")
HOME = os.path.expanduser("~")


def hypr_dir():
    base = os.path.join(RUNTIME, "hypr")
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig and os.path.isdir(os.path.join(base, sig)):
        return os.path.join(base, sig)
    dirs = [os.path.join(base, d) for d in os.listdir(base)] if os.path.isdir(base) else []
    dirs = [d for d in dirs if os.path.exists(os.path.join(d, ".socket2.sock"))]
    return max(dirs, key=os.path.getmtime) if dirs else None


def request(cmd):
    """One Hyprland socket request (like `hyprctl -j <cmd>`), straight to the socket: no process."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    s.connect(os.path.join(hypr_dir(), ".socket.sock"))
    s.sendall(f"j/{cmd}".encode())
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    s.close()
    return json.loads(out or b"null")


def palette():
    try:
        c = json.load(open(os.path.join(HOME, ".cache/wallpaper-theme/.colors.json")))["colors"]
        col = lambda k: c[k]["default"]["color"]
        return col("primary"), col("yellow"), col("background")
    except (OSError, ValueError, KeyError):
        return "#d99a3e", "#f2b85c", "#0f0e0c"


def rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def run():
    import cairo
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

    amber, amber_hi, ink = (rgb(h) for h in palette())
    anims = []           # {"kind", "x", "y", "w", "h", "t0"} in global layout coordinates
    geometry = {}        # window address -> (x, y, w, h), kept fresh for the close animation
    overlays = {}        # monitor name -> (window, x, y, w, h)

    display = Gdk.Display.get_default()

    def build_overlays():
        for win, *_ in overlays.values():
            win.destroy()
        overlays.clear()
        for m in request("monitors") or []:
            gmon = None
            for i in range(display.get_n_monitors()):
                g = display.get_monitor(i).get_geometry()
                if (g.x, g.y) == (m["x"], m["y"]):
                    gmon = display.get_monitor(i)
            if not gmon:
                continue
            w = Gtk.Window()
            w.set_app_paintable(True)
            visual = w.get_screen().get_rgba_visual()
            if visual:
                w.set_visual(visual)
            GtkLayerShell.init_for_window(w)
            GtkLayerShell.set_namespace(w, "workshop-unfold")
            GtkLayerShell.set_layer(w, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_monitor(w, gmon)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(w, edge, True)
            GtkLayerShell.set_exclusive_zone(w, -1)
            GtkLayerShell.set_keyboard_mode(w, GtkLayerShell.KeyboardMode.NONE)
            w.connect("realize", lambda win: win.input_shape_combine_region(cairo.Region()))   # clicks fall through
            ox, oy = m["x"], m["y"]
            w.connect("draw", lambda win, cr, ox=ox, oy=oy: draw(cr, ox, oy))
            overlays[m["name"]] = (w, m["x"], m["y"], m["width"], m["height"])

    def line(cr, x0, y0, x1, y1, color, alpha):
        # a glowing hairline: two soft passes under a crisp one
        for width, a in ((7, 0.12), (3.5, 0.3), (1.5, 1.0)):
            cr.set_source_rgba(*color, alpha * a)
            cr.set_line_width(width)
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.stroke()

    def frame_at(a, now):
        """(half width, half height, line alpha, unused) of one animation at `now`, or None when done."""
        t = (now - a["t0"]) * 1000
        W, H = a["w"] / 2, a["h"] / 2
        if a["kind"] == "open":
            if t < LINE_MS:
                return W * ease_out(t / LINE_MS), 0, 1, 0
            if t < OPEN_MS:
                return W, H * ease_in_out((t - LINE_MS) / (OPEN_MS - LINE_MS)), 1, 0.5
            if t < OPEN_MS + FADE_MS:
                f = 1 - (t - OPEN_MS) / FADE_MS
                return W, H, f, 0.5 * f * f
            return None
        if t < FOLD_MS:
            return W, H * (1 - ease_in_out(t / FOLD_MS)), 1, 0.5
        if t < CLOSE_MS:
            return W * (1 - ease_out((t - FOLD_MS) / (CLOSE_MS - FOLD_MS))), 0, 1, 0
        return None

    def draw(cr, ox, oy):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        now = time.monotonic()
        for a in anims:
            f = frame_at(a, now)
            if not f:
                continue
            hw, hh, la, _ = f
            cx, cy = a["x"] + a["w"] / 2 - ox, a["y"] + a["h"] / 2 - oy
            if hh < 0.5:
                line(cr, cx - hw, cy, cx + hw, cy, amber_hi, la)
                cr.set_source_rgba(*amber_hi, la)             # the point the line grows from
                cr.arc(cx, cy, 2.5, 0, 2 * math.pi)
                cr.fill()
                continue
            # outline only: a fill read as a second window opening before the real one
            line(cr, cx - hw, cy - hh, cx + hw, cy - hh, amber_hi, la)   # the moving edges glow
            line(cr, cx - hw, cy + hh, cx + hw, cy + hh, amber_hi, la)
            cr.set_source_rgba(*amber, 0.9 * la)                     # the sides are plain hairlines
            cr.set_line_width(1)
            cr.move_to(cx - hw + 0.5, cy - hh); cr.line_to(cx - hw + 0.5, cy + hh)
            cr.move_to(cx + hw - 0.5, cy - hh); cr.line_to(cx + hw - 0.5, cy + hh)
            cr.stroke()

    ticking = [False]

    def tick():
        now = time.monotonic()
        anims[:] = [a for a in anims if frame_at(a, now)]
        for name, (w, x, y, mw, mh) in overlays.items():
            busy = any(a["x"] < x + mw and a["x"] + a["w"] > x and a["y"] < y + mh and a["y"] + a["h"] > y for a in anims)
            if busy:
                if not w.get_visible():
                    w.show_all()
                w.queue_draw()
            elif w.get_visible():
                w.hide()          # unmapped when idle: no overlay over games/fullscreen video
        if anims:
            return True
        ticking[0] = False
        return False

    def start(kind, x, y, w, h):
        anims.append({"kind": kind, "x": x, "y": y, "w": w, "h": h, "t0": time.monotonic()})
        if not ticking[0]:
            ticking[0] = True
            GLib.timeout_add(16, tick)
        return False

    def refresh():
        """Where every mapped window is, and which workspaces are on screen."""
        try:
            clients = request("clients") or []
            shown = {m["activeWorkspace"]["id"] for m in request("monitors") or []}
        except (OSError, ValueError):
            return None, set()
        for c in clients:
            geometry[c["address"]] = (c["at"][0], c["at"][1], c["size"][0], c["size"][1], c["workspace"]["id"], c["fullscreen"])
        return clients, shown

    def events():
        """Hyprland's event stream (socket2), on its own thread; drawing is handed to the GTK loop."""
        while True:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(os.path.join(hypr_dir(), ".socket2.sock"))
                refresh()
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        ev, _, data = raw.decode("utf-8", "replace").partition(">>")
                        if ev == "openwindow":
                            addr = "0x" + data.split(",", 1)[0]
                            _, shown = refresh()
                            g = geometry.get(addr)
                            if g and g[4] in shown and not g[5] and g[2] > 20 and g[3] > 20:
                                GLib.idle_add(start, "open", *g[:4])
                            threading.Timer(0.8, refresh).start()   # the others have re-tiled by then
                        elif ev == "closewindow":
                            addr = "0x" + data.strip()
                            g = geometry.pop(addr, None)
                            _, shown = refresh()
                            if g and g[4] in shown and not g[5] and g[2] > 20 and g[3] > 20:
                                GLib.idle_add(start, "close", *g[:4])
                        elif ev in ("movewindow", "movewindowv2", "changefloatingmode", "fullscreen", "workspace",
                                    "workspacev2", "activewindowv2", "focusedmon", "resizewindow"):
                            refresh()
                            # windows move and settle after the event; look again once they have
                            threading.Timer(0.35, refresh).start()
                        elif ev in ("monitoradded", "monitorremoved", "monitoraddedv2", "monitorremovedv2"):
                            GLib.idle_add(lambda: build_overlays() or False)
            except OSError:
                pass
            time.sleep(1)   # Hyprland restarted or the socket went away: try again

    build_overlays()
    threading.Thread(target=events, daemon=True).start()
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: Gtk.main_quit() or False)
    try:
        Gtk.main()
    finally:
        try:
            if int(open(PIDFILE).read()) == os.getpid():
                os.remove(PIDFILE)
        except (OSError, ValueError):
            pass


def running():
    try:
        pid = int(open(PIDFILE).read())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            if b"unfold.py" in f.read():
                return pid
    except (OSError, ValueError):
        pass
    return None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run":
        run()
    elif cmd == "on":
        if not running():
            log = open(os.path.join(RUNTIME, "workshop-unfold.log"), "ab")
            subprocess.Popen([sys.executable, os.path.realpath(__file__), "run"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    elif cmd == "off":
        pid = running()
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                if not running():
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
    else:
        sys.exit(__doc__)
