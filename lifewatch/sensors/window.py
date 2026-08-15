"""What application and document currently has focus.

The reader shells out to ``xprop``, which is present on the target machine and
needs no permissions, no root and no new package. It is a subprocess rather than
an Xlib binding because the binding would be a dependency for two string reads,
and because a subprocess can be given a timeout that the poll loop can rely on.

The sensor itself never looks at the title it reports. A title is evidence, and
deciding what it means is the classifier's job.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from typing import Callable, Optional, Tuple

from lifewatch.models import Observation

WindowReader = Callable[[], Optional[Tuple[str, str]]]

# Well under the 15s poll interval, so a wedged X server delays a reading
# rather than stalling every other sensor behind it.
_XPROP_TIMEOUT_S = 5

_ACTIVE_WINDOW_ID = re.compile(r"window id # (0x[0-9a-fA-F]+)")
# Digits belong in both groups: the property type is routinely UTF8_STRING, and
# a pattern that forbids the 8 falls through to WM_NAME's COMPOUND_TEXT, which
# mangles any title that is not ASCII.
_PROPERTY_LINE = re.compile(
    r"^(?P<name>[A-Z0-9_]+)\((?P<type>[A-Z0-9_]+)\) = (?P<body>.*)$", re.MULTILINE
)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _unescape(raw: str) -> str:
    """Undo xprop's escaping of a quoted string.

    Done in one pass rather than by chained ``replace`` calls: a title
    containing a literal backslash before a quote would otherwise be mangled by
    whichever replacement ran second.
    """
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), raw)


def _quoted_values(output: str, prop: str) -> list[str]:
    for match in _PROPERTY_LINE.finditer(output):
        if match.group("name") == prop:
            return [_unescape(m.group(1)) for m in _QUOTED.finditer(match.group("body"))]
    return []


def _xprop(args: list[str], tolerate_failure: bool = False) -> Optional[str]:
    """Run xprop and return stdout, or ``None`` when a tolerated call failed.

    A missing binary or an unreachable display raises, which is how
    ``available()`` learns this machine cannot answer the question at all.
    """
    result = subprocess.run(
        ["xprop", *args],
        capture_output=True,
        text=True,
        timeout=_XPROP_TIMEOUT_S,
    )
    if result.returncode != 0:
        if tolerate_failure:
            return None
        raise OSError(
            f"xprop {' '.join(args)} failed: {result.stderr.strip() or 'no output'}"
        )
    return result.stdout


def read_active_window() -> Optional[Tuple[str, str]]:
    """Return ``(wm_class, title)`` for the focused window, or ``None``.

    ``None`` means nothing has focus, which is an answer. Failure to ask at all
    raises instead, so a dead X connection is never mistaken for an empty desk.
    """
    root = _xprop(["-root", "_NET_ACTIVE_WINDOW"]) or ""
    match = _ACTIVE_WINDOW_ID.search(root)
    if not match:
        return None
    window_id = match.group(1)
    if int(window_id, 16) == 0:
        # X11 reports 0x0 when the pointer is on the desktop or a window is
        # closing. Nothing is focused; that is not an error.
        return None

    # Tolerated: the focused window can vanish between the two calls, and a
    # window that no longer exists is a race, not a broken display server.
    props = _xprop(
        ["-id", window_id, "_NET_WM_NAME", "WM_NAME", "WM_CLASS"],
        tolerate_failure=True,
    )
    if props is None:
        return None

    names = _quoted_values(props, "_NET_WM_NAME") or _quoted_values(props, "WM_NAME")
    title = names[0] if names else ""
    # WM_CLASS is `"instance", "Class"`; the second is the one applications
    # share across windows, which is what a rule wants to match on.
    classes = _quoted_values(props, "WM_CLASS")
    wm_class = classes[-1] if classes else ""

    if not title and not wm_class:
        return None
    return (wm_class, title)


class WindowSensor:
    """Reports the focused window as ``class|title``.

    The class comes first so the application is recoverable by splitting on the
    first separator even when the title contains one, and so a rule about an
    application is a prefix match rather than a parse.
    """

    name = "window"

    def __init__(
        self,
        reader: WindowReader = read_active_window,
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
        """Read once. Exceptions travel up to the runner, which isolates them.

        Swallowing a failure here would make a broken sensor look exactly like
        an unfocused desktop, and an instrument that dies quietly is worse than
        no instrument.
        """
        focused = self.reader()
        if focused is None:
            return []
        wm_class, title = focused
        return [
            Observation(
                ts=now,
                sensor=self.name,
                kind="focus",
                value=f"{wm_class}|{title}",
                meta={"wm_class": wm_class, "title": title},
            )
        ]
