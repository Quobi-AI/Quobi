"""Post-install health check: `voice-type --check` (`make restart` runs it).

Run after every daemon install/restart. It waits for the daemon to come up,
then confirms the keyboard was left clean: nothing stuck on a virtual device
and no modifier stranded in the compositor. What it can repair it repairs
(pass --no-fix to only report). Prints one line and exits 0 when all is well,
exits 1 with the reasons otherwise. Linux only.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import load

# Device-name fragments, matching input.py's replay keyboard and ydotoold.
VIRTUAL_KBD = "voice-type virtual"
YDOTOOL_DEV = "ydotool"

# systemd's unit for the autostart entry quobi-daemon.desktop ('-' -> \x2d);
# the same one the Quobi app starts and stops (daemonctl.rs DAEMON_UNIT).
DAEMON_UNIT = "app-quobi\\x2ddaemon@autostart.service"

# Modifiers the compositor probe reports -> the evdev key codes that hold them.
MODIFIER_CODES = {
    "Shift": (42, 54),
    "Control": (29, 97),
    "Alt": (56, 100),
    "Meta": (125, 126),
}

_START_RE = re.compile(r"voice-type \S+ starting \(")
_READY_MARK = "] ready. "
_STOP_MARK = "-> shutting down"
_ERROR_MARK = "[ERROR]"

# Reads KWin's own idea of which modifiers are physically down (its key-state
# protocol, via the Plasma keyboard-indicator QML module). Headless: no window.
_MODSTATE_QML = """\
import QtQml
import org.kde.plasma.private.keyboardindicator as KI
QtObject {
    property var shift: KI.KeyState { key: Qt.Key_Shift }
    property var control: KI.KeyState { key: Qt.Key_Control }
    property var alt: KI.KeyState { key: Qt.Key_Alt }
    property var meta: KI.KeyState { key: Qt.Key_Meta }
    property var timer: Timer {
        interval: 500; running: true
        onTriggered: {
            console.log("MODSTATE Shift=" + shift.pressed + " Control=" + control.pressed
                        + " Alt=" + alt.pressed + " Meta=" + meta.pressed)
            Qt.quit()
        }
    }
}
"""


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Modifiers the compositor holds down that no device is holding.
    stranded: list[str] = field(default_factory=list)
    # Keys stuck down on a virtual device, by device name.
    stuck: dict[str, set[int]] = field(default_factory=dict)
    pids: dict[int, list[str]] = field(default_factory=dict)
    run: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def repairable(self) -> bool:
        return bool(self.stranded or self.stuck)


# ---- pure decision logic (unit-tested) -------------------------------------


def current_run(lines: list[str]) -> list[str]:
    """Log lines of the most recent daemon start."""
    for i in range(len(lines) - 1, -1, -1):
        if _START_RE.search(lines[i]):
            return lines[i:]
    return []


def log_state(run: list[str]) -> str:
    """Where one daemon run got to: none / starting / ready / stopped / error.
    An error only counts before `ready`; later ones are runtime noise."""
    if not run:
        return "none"
    state = "starting"
    for line in run:
        if _STOP_MARK in line:
            return "stopped"
        if _READY_MARK in line:
            state = "ready"
        elif _ERROR_MARK in line and state != "ready":
            return "error"
    return state


def stranded_modifiers(pressed: dict[str, bool], held_codes: set[int]) -> list[str]:
    """Modifiers the compositor believes are down that no device is holding."""
    return [
        mod for mod, down in pressed.items()
        if down and not held_codes.intersection(MODIFIER_CODES.get(mod, ()))
    ]


def _is_virtual(name: str) -> bool:
    name = name.lower()
    return VIRTUAL_KBD in name or YDOTOOL_DEV in name


def stuck_virtual_keys(held: dict[str, set[int]]) -> dict[str, set[int]]:
    """Keys held on a virtual device that no real keyboard is holding. (A key
    the user is holding shows on the replay keyboard too; that is not stuck.)"""
    real: set[int] = set()
    for name, keys in held.items():
        if not _is_virtual(name):
            real |= keys
    return {
        name: keys - real
        for name, keys in held.items()
        if _is_virtual(name) and keys - real
    }


def parse_modstate(output: str) -> dict[str, bool] | None:
    """Parse the QML probe's `MODSTATE Shift=true ...` line; None if absent."""
    m = re.search(r"MODSTATE (.+)", output)
    if not m:
        return None
    return {
        k: v == "true"
        for k, v in (pair.split("=", 1) for pair in m.group(1).split() if "=" in pair)
    }


# ---- probes -----------------------------------------------------------------


def _system_env() -> dict[str, str]:
    """Environment for spawning system programs (and a fresh daemon) from the
    frozen binary: undo PyInstaller's LD_LIBRARY_PATH override, and make a
    re-launched voice-type unpack itself instead of borrowing our temp dir."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def daemon_pids() -> dict[int, list[str]]:
    """Running daemon processes (pid -> argv). The frozen binary shows up as
    two: PyInstaller's bootloader and the daemon proper."""
    found: dict[int, list[str]] = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            raw = (p / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if "--daemon" in argv and any(
            "voice-type" in a or "voice_type" in a for a in argv[:3]
        ):
            found[int(p.name)] = argv
    return found


def _proc_start_epoch(pid: int) -> float | None:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
        ticks = int(stat.rsplit(")", 1)[1].split()[19])
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1]) + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        pass
    return None


def _run_predates(run: list[str], pids: dict[int, list[str]]) -> bool:
    """True if the log's latest run was written by an earlier daemon than the
    one running now (a just-launched daemon that hasn't logged yet). The log
    carries time of day only, so compare on that, wrapping at midnight."""
    starts = [s for s in (_proc_start_epoch(p) for p in pids) if s is not None]
    m = re.match(r"(\d\d):(\d\d):(\d\d) ", run[0]) if run else None
    if not starts or not m:
        return False
    logged = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    t = time.localtime(min(starts))
    started = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
    diff = (logged - started + 43200) % 86400 - 43200
    return diff < -10


def _read_log(path: Path) -> list[str]:
    lines: list[str] = []
    # Oldest first: the RotatingFileHandler backups, then the live file.
    for p in (path.with_name(path.name + ".2"), path.with_name(path.name + ".1"), path):
        try:
            lines += p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            pass
    return lines


def _wait_for_daemon(log_path: Path | None, timeout: float = 45.0):
    """Wait until the running daemon has logged `ready` (or clearly failed).
    Returns (state, run_lines, pids)."""
    deadline = time.monotonic() + timeout
    bad_since: float | None = None
    while True:
        pids = daemon_pids()
        run = current_run(_read_log(log_path)) if log_path else []
        state = log_state(run)
        if pids and _run_predates(run, pids):
            state, run = "starting", []
        if pids and state == "none":
            # No start line: no log file, or it rotated out during a long run.
            age = time.time() - min(
                (s for s in map(_proc_start_epoch, pids) if s is not None),
                default=time.time(),
            )
            if not log_path or age > 60:
                return "running", run, pids
        if pids and state == "ready":
            return state, run, pids
        # A dead daemon or a startup error only counts once it has held for a
        # few seconds: right after a relaunch the log still shows the old run.
        if not pids or state == "error":
            bad_since = bad_since or time.monotonic()
            if time.monotonic() - bad_since > 3.0:
                return state, run, pids
        else:
            bad_since = None
        if time.monotonic() > deadline:
            return state, run, pids
        time.sleep(0.5)


def _open_keyboards() -> list:
    import evdev
    from evdev import ecodes

    keyboards = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if ecodes.KEY_A in dev.capabilities().get(ecodes.EV_KEY, []):
            keyboards.append(dev)
        else:
            dev.close()
    return keyboards


def _held_now(devs: list) -> dict[str, set[int]]:
    held: dict[str, set[int]] = {}
    for d in devs:
        try:
            held.setdefault(d.name, set()).update(d.active_keys())
        except OSError:
            pass
    return held


def _held_throughout(devs: list, samples: int = 8, interval: float = 0.15) -> dict[str, set[int]]:
    """Keys that stayed down on each device for the whole sampling window."""
    held = _held_now(devs)
    for _ in range(samples - 1):
        time.sleep(interval)
        now = _held_now(devs)
        held = {name: keys & now.get(name, set()) for name, keys in held.items()}
    return held


def _all_held(devs: list) -> set[int]:
    codes: set[int] = set()
    for keys in _held_now(devs).values():
        codes |= keys
    return codes


def compositor_modifiers() -> dict[str, bool] | None:
    """Modifiers the compositor considers physically pressed, or None where we
    can't ask (needs KDE Plasma and the `qml6` runner)."""
    qml = shutil.which("qml6") or shutil.which("qml")
    if not qml or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return None
    env = _system_env()
    env.update(QT_FORCE_STDERR_LOGGING="1", QT_LOGGING_RULES="qml.debug=true")
    with tempfile.TemporaryDirectory(prefix="voice-type-check-") as tmp:
        probe = Path(tmp) / "modstate.qml"
        probe.write_text(_MODSTATE_QML)
        try:
            r = subprocess.run([qml, str(probe)], capture_output=True, text=True,
                               timeout=10, env=env)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return parse_modstate(r.stdout + r.stderr)


def _cleanup_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def _key_names(codes) -> str:
    from evdev import ecodes

    names = []
    for code in sorted(codes):
        name = ecodes.KEY.get(code, str(code))
        names.append(name if isinstance(name, str) else name[0])
    return ", ".join(names)


# ---- the check --------------------------------------------------------------


def _check(cfg, log_path: Path | None) -> Report:
    rep = Report()
    state, rep.run, rep.pids = _wait_for_daemon(log_path)

    if state in ("ready", "running") and rep.pids:
        rep.passed.append(f"daemon {state} (pid {min(rep.pids)})")
    elif state == "error":
        errors = [ln for ln in rep.run if _ERROR_MARK in ln]
        rep.failed.append("daemon failed to start: " + (errors[-1] if errors else "see log"))
    elif not rep.pids:
        rep.failed.append("daemon is not running")
    else:
        rep.failed.append("daemon is running but never logged 'ready' "
                          f"(last line: {rep.run[-1] if rep.run else 'none'})")

    # Startup lines worth surfacing: what the grab had to do, and what it couldn't.
    for line in rep.run:
        if "could not grab" in line or "anti-strand: could not release" in line:
            rep.failed.append("keyboard grab problem: " + line.split("] ", 1)[-1])
        elif "anti-strand: released" in line or "still held after" in line:
            rep.notes.append(line.split("] ", 1)[-1])

    devs = _open_keyboards()
    try:
        if state == "ready" and any("evdev: grabbed" in ln for ln in rep.run):
            if any(VIRTUAL_KBD in d.name.lower() for d in devs):
                rep.passed.append("replay keyboard up")
            else:
                rep.failed.append("daemon grabbed the keyboard but its replay "
                                  "keyboard is gone; typing will not get through")

        rep.stuck = stuck_virtual_keys(_held_throughout(devs))
        for name, keys in rep.stuck.items():
            rep.failed.append(f"key stuck down on {name}: {_key_names(keys)}")
        if not rep.stuck:
            rep.passed.append("no stuck keys")

        # A modifier only counts as stranded if the compositor reports it down
        # twice running while no device holds it; a single hit is the user
        # tapping it mid-probe.
        pressed = None
        for _ in range(2):
            before = _all_held(devs)
            pressed = compositor_modifiers()
            if pressed is None:
                break
            rep.stranded = stranded_modifiers(pressed, before | _all_held(devs))
            if not rep.stranded:
                break
            time.sleep(0.3)
        if pressed is None:
            rep.notes.append("compositor modifier state not checked "
                             "(needs KDE Plasma and qml6)")
        elif rep.stranded:
            rep.failed.append("stuck in the compositor with no key held: "
                              + ", ".join(rep.stranded))
        else:
            rep.passed.append("no stranded modifiers")
    finally:
        for d in devs:
            d.close()

    if state == "ready" and cfg.cleanup.enabled:
        ready = next(ln for ln in reversed(rep.run) if _READY_MARK in ln)
        if "cleanup=on" not in ready:
            rep.failed.append("cleanup is enabled in config but did not start "
                              "(raw transcription only)")
        elif _cleanup_healthy(cfg.cleanup.local_port):
            rep.passed.append("cleanup model up")
        else:
            rep.failed.append(f"cleanup server on port {cfg.cleanup.local_port} "
                              "is not answering")
    return rep


# ---- repair -----------------------------------------------------------------


def _stop_daemon(pids: dict[int, list[str]], timeout: float = 15.0) -> bool:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not set(pids) & set(daemon_pids()):
            return True
        time.sleep(0.2)
    return False


def _tap_on_real_keyboards(codes: list[int]) -> list[str]:
    """Press and release `codes` on every real keyboard's own event node. Where
    the compositor has the key stranded, it ignores the DOWN (already down) and
    the UP clears it; anywhere else it is a bare modifier tap. Only works with
    the daemon stopped: a grabbed device drops injected events."""
    from evdev import EvdevError, ecodes

    tapped = []
    for dev in _open_keyboards():
        try:
            if _is_virtual(dev.name):
                continue
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            for code in codes:
                if code not in caps:
                    continue
                # Plain EV_SYN writes: the evdev we bundle has no InputDevice.syn().
                dev.write(ecodes.EV_KEY, code, 1)
                dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
                time.sleep(0.03)
                dev.write(ecodes.EV_KEY, code, 0)
                dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
                time.sleep(0.03)
            tapped.append(dev.name)
        except (OSError, EvdevError):
            pass
        finally:
            dev.close()
    return tapped


def _release_on_ydotool(codes: set[int], run: list[str]) -> bool:
    env = _system_env()
    for line in reversed(run):
        if "ydotool socket: " in line:
            env.setdefault("YDOTOOL_SOCKET", line.rsplit("ydotool socket: ", 1)[1].strip())
            break
    try:
        r = subprocess.run(["ydotool", "key", *[f"{c}:0" for c in sorted(codes)]],
                           capture_output=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _start_daemon(argv: list[str], cwd: str | None) -> None:
    """Relaunch the daemon. Prefer its systemd user unit (generated from the
    autostart entry; what login and the Quobi app use) when that unit runs this
    same command, so the daemon stays its own app in the session instead of
    becoming a child of whatever ran the check."""
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    try:
        entry = (config / "autostart" / "quobi-daemon.desktop").read_text()
        exec_line = next(ln[5:] for ln in entry.splitlines() if ln.startswith("Exec="))
        unit_runs_this = shlex.split(exec_line) == argv
    except (OSError, StopIteration, ValueError):
        unit_runs_this = False
    if unit_runs_this and shutil.which("systemctl"):
        r = subprocess.run(["systemctl", "--user", "start", DAEMON_UNIT],
                           capture_output=True, env=_system_env())
        if r.returncode == 0:
            return
    subprocess.Popen(argv, cwd=cwd, env=_system_env(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _repair(rep: Report) -> list[str]:
    done: list[str] = []
    ydotool = {n: k for n, k in rep.stuck.items() if YDOTOOL_DEV in n.lower()}
    replay = {n: k for n, k in rep.stuck.items() if n not in ydotool}

    if rep.stranded or replay:
        # Both need the daemon down: its grab blocks injection on the real
        # keyboards, and closing its replay keyboard releases whatever is
        # stuck on that.
        launch = None
        if rep.pids:
            pid = min(rep.pids)
            try:
                cwd = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                cwd = None
            launch = (rep.pids[pid], cwd)
            if not _stop_daemon(rep.pids):
                return ["could not stop the daemon to repair; nothing changed"]
        try:
            if rep.stranded:
                codes = [c for mod in rep.stranded for c in MODIFIER_CODES[mod]]
                tapped = _tap_on_real_keyboards(codes)
                done.append(f"released {', '.join(rep.stranded)} on "
                            f"{', '.join(tapped) or 'no device'}")
            if replay:
                done.append("dropped the replay keyboard holding "
                            + _key_names(set().union(*replay.values())))
        finally:
            # Whatever happened above, never leave the user without a daemon.
            if launch:
                _start_daemon(*launch)
                done.append("restarted the daemon")

    for name, keys in ydotool.items():
        if _release_on_ydotool(keys, rep.run):
            done.append(f"released {_key_names(keys)} on {name}")
    return done


# ---- entry point -------------------------------------------------------------


def run(args: list[str]) -> int:
    if not sys.platform.startswith("linux"):
        print("voice-type --check: only implemented on Linux")
        return 0
    fix = "--no-fix" not in args
    verbose = "--verbose" in args or "-v" in args

    cfg = load()
    log_path = (
        Path(os.path.expanduser(os.path.expandvars(cfg.log.file))) if cfg.log.file else None
    )

    rep = _check(cfg, log_path)
    repairs: list[str] = []
    if fix and rep.repairable:
        for line in rep.failed:
            print(f"  ✘ {line}")
        print("  → repairing")
        repairs = _repair(rep)
        rep = _check(cfg, log_path)

    for line in repairs:
        print(f"  ✔ repair: {line}")
    for line in rep.notes:
        print(f"  · {line}")
    if rep.ok:
        if verbose:
            for line in rep.passed:
                print(f"  ✔ {line}")
        print("  ✔ check passed: " + ", ".join(rep.passed))
        return 0
    if verbose:
        for line in rep.passed:
            print(f"  ✔ {line}")
    for line in rep.failed:
        print(f"  ✘ {line}")
    print("  ✘ check FAILED" + ("" if fix else " (run without --no-fix to repair)"))
    return 1
