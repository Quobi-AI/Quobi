"""Cursor-anchored floating status pill.

Runs tkinter on its own thread; updates from other threads are marshaled via
`Tk.after(0, ...)`. Designed to be invisible when idle and only surface during
recording/transcribing/formatting/error states.
"""
from __future__ import annotations

import threading
import time

from .indicator import Indicator, State, _STATE_COLOR
from .log import log


class FloatingOverlay(Indicator):
    def __init__(self) -> None:
        # Probe tkinter at construction so make_indicator() can fall back.
        import tkinter  # noqa: F401
        self._state = State.IDLE
        self._detail = ""
        self._mic_level = 0.0
        self._last_mic_marshal = 0.0
        self._ready = threading.Event()
        self._root = None
        self._top = None
        self._canvas = None
        self._W = 280
        self._H = 56
        self._thread: threading.Thread | None = None

    # --- Indicator API --------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="overlay")
        self._thread.start()
        # If tkinter blocks coming up (X server slow), don't hang the daemon.
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        if self._root is not None:
            try:
                self._root.after(0, self._root.quit)
            except Exception:  # noqa: BLE001
                pass

    def set_state(self, state: State, detail: str = "") -> None:
        self._state = state
        self._detail = detail
        if state != State.RECORDING:
            self._mic_level = 0.0
        self._marshal()

    def update_detail(self, state: State, detail: str) -> None:
        if state != self._state:
            return self.set_state(state, detail)
        self._detail = detail
        self._marshal()

    def set_mic_level(self, level: float) -> None:
        self._mic_level = max(0.0, min(1.0, level))
        # Throttle marshals to ~20 Hz so Tk's queue doesn't grow under load.
        now = time.monotonic()
        if now - self._last_mic_marshal > 0.05 and self._state == State.RECORDING:
            self._last_mic_marshal = now
            self._marshal()

    def notify(self, title: str, body: str, urgent: bool = False) -> None:
        # The overlay shows state in-place; popups are someone else's job.
        pass

    # --- tk thread ------------------------------------------------------

    def _marshal(self) -> None:
        if self._root is None:
            return
        try:
            self._root.after(0, self._redraw)
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            log().warning("tkinter not available; overlay disabled")
            self._ready.set()
            return

        try:
            root = tk.Tk()
        except tk.TclError as e:
            log().warning("overlay: cannot open display (%s); disabled", e)
            self._ready.set()
            return

        root.withdraw()  # Hide the implicit root window.

        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        for attr, val in (("-alpha", 0.93), ("-type", "splash")):
            try:
                top.attributes(attr, val)
            except tk.TclError:
                pass

        screen_w = top.winfo_screenwidth()
        screen_h = top.winfo_screenheight()
        x = (screen_w - self._W) // 2
        y = screen_h - self._H - 90
        top.geometry(f"{self._W}x{self._H}+{x}+{y}")

        canvas = tk.Canvas(
            top, width=self._W, height=self._H,
            bg="#0d0d10", highlightthickness=0, bd=0,
        )
        canvas.pack(fill="both", expand=True)

        self._root = root
        self._top = top
        self._canvas = canvas
        top.withdraw()  # invisible until state != IDLE
        self._ready.set()

        # Initial paint (will withdraw because IDLE).
        self._redraw()
        try:
            root.mainloop()
        except Exception as e:  # noqa: BLE001
            log().debug("overlay mainloop ended: %s", e)
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._root = None
            self._top = None
            self._canvas = None

    def _redraw(self) -> None:
        top = self._top
        canvas = self._canvas
        if top is None or canvas is None:
            return

        if self._state == State.IDLE:
            try:
                top.withdraw()
            except Exception:  # noqa: BLE001
                pass
            return

        try:
            top.deiconify()
            top.lift()
        except Exception:  # noqa: BLE001
            pass

        c = canvas
        W, H = self._W, self._H
        cr, cg, cb = _STATE_COLOR[self._state]
        color = f"#{cr:02x}{cg:02x}{cb:02x}"

        c.delete("all")

        # Status dot — pulses subtly during recording via mic level.
        cx = 22
        cy = H // 2
        base_r = 6
        boost = int(self._mic_level * 3) if self._state == State.RECORDING else 0
        r = base_r + boost
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

        # State + detail text.
        label = self._state.value
        if self._detail:
            label = f"{label}   ·   {self._detail}"
        c.create_text(
            42, cy, anchor="w", text=label,
            fill="#f1f1f4", font=("DejaVu Sans", 11, "normal"),
        )

        # Mic-level bar — only meaningful while recording.
        if self._state == State.RECORDING:
            bar_x0, bar_x1 = W - 84, W - 18
            by = H // 2
            c.create_rectangle(
                bar_x0, by - 3, bar_x1, by + 3,
                fill="#1f1f24", outline="",
            )
            fill_w = int((bar_x1 - bar_x0) * self._mic_level)
            if fill_w > 0:
                c.create_rectangle(
                    bar_x0, by - 3, bar_x0 + fill_w, by + 3,
                    fill=color, outline="",
                )
