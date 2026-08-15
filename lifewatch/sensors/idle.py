"""How long since the last keyboard or mouse input.

The X Screen Saver extension already tracks this for its own purposes, so the
number is free: no key logging, no input hooks, no elevated permission. What is
recorded is a duration and nothing about what was typed.

Reached through ``ctypes`` rather than a Python X binding because the binding
would be a build dependency for one struct field, and because the extension is
present on the target machine while the binding is not.
"""

from __future__ import annotations

import ctypes
from datetime import datetime
from typing import Callable, Optional

from lifewatch.models import Observation

IdleReader = Callable[[], float]


class _XScreenSaverInfo(ctypes.Structure):
    """Mirrors the C struct from ``X11/extensions/scrnsaver.h``.

    Field order and width must match exactly; ``idle`` is read by offset, so a
    wrong type here would return a plausible but meaningless number rather than
    failing loudly.
    """

    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("event_mask", ctypes.c_ulong),
    ]


_libs: Optional[tuple] = None


def _load_libs() -> tuple:
    """Load and configure the two shared libraries once per process.

    Cached because a poll every 15 seconds for a whole term should not re-dlopen
    Xlib each time, and because the signature setup below is what stops ctypes
    from truncating a 64-bit pointer to an int.
    """
    global _libs
    if _libs is None:
        xlib = ctypes.CDLL("libX11.so.6")
        xss = ctypes.CDLL("libXss.so.1")

        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XFree.argtypes = [ctypes.c_void_p]

        xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
        xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_XScreenSaverInfo),
        ]
        xss.XScreenSaverQueryInfo.restype = ctypes.c_int

        _libs = (xlib, xss)
    return _libs


def read_idle_ms() -> int:
    """Milliseconds since the last input event.

    The display connection is opened and closed per call rather than held. A
    connection kept open across a suspend or a session restart goes stale and
    every later reading is a lie; reopening costs microseconds and is
    self-healing.
    """
    xlib, xss = _load_libs()
    display = xlib.XOpenDisplay(None)
    if not display:
        raise OSError("cannot open an X display; is DISPLAY set?")
    info = None
    try:
        info = xss.XScreenSaverAllocInfo()
        if not info:
            raise OSError("XScreenSaverAllocInfo returned NULL")
        root = xlib.XDefaultRootWindow(display)
        if not xss.XScreenSaverQueryInfo(display, root, info):
            raise OSError("XScreenSaverQueryInfo failed; is the extension present?")
        return int(info.contents.idle)
    finally:
        if info:
            xlib.XFree(info)
        xlib.XCloseDisplay(display)


class IdleSensor:
    """Reports milliseconds since the last input.

    It holds no threshold. Whether twenty minutes of silence means absent, or
    means reading a textbook, is a judgment, and judgments live in the
    classifier where they can be configured and corrected.
    """

    name = "idle"

    def __init__(
        self,
        reader: IdleReader = read_idle_ms,
        poll_interval_s: int = 15,
    ) -> None:
        self.reader = reader
        self.poll_interval_s = poll_interval_s

    def available(self) -> bool:
        try:
            self.reader()
        except Exception:
            return False
        return True

    def poll(self, now: datetime) -> list[Observation]:
        # Coerced to int because the store holds strings and every consumer
        # parses this one back with int(); a float would reach them as a
        # ValueError at classification time instead of here.
        idle_ms = int(self.reader())
        return [
            Observation(
                ts=now,
                sensor=self.name,
                kind="ms",
                value=str(idle_ms),
                meta={},
            )
        ]
