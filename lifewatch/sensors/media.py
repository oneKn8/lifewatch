"""Whether anything is playing, and which application is playing it.

This sensor exists for one rule: spec section 7.1, media playing while a
*different* window has focus is listening rather than watching. That rule is
the highest-value line in Tier 1 because it resolves the study-music versus
entertainment ambiguity structurally, with no title classified and no model
asked. Without a sensor emitting ``media`` the rule cannot fire at all, so this
module is what makes it real rather than theoretical.

**The identity of what is playing is never read.** MPRIS offers a ``Metadata``
property carrying track title, artist, album and URL. It is not requested, not
parsed and not recorded. The question here is "is something playing", and the
answer is a state word; knowing *what* is playing would add nothing to the
verdict while creating the single most sensitive record the system could hold.
The default reader asks for exactly one property, by name, and a test asserts
that ``Metadata`` never appears in a command it issues.

**The playing process is recorded, and that is not the same thing.** Tier 1
needs to tell two cases apart: media playing behind a text editor is ambience,
while media playing *in the focused window* is a question only a look at the
title can settle, so Tier 1 declines and Tier 2 judges it.

Identity must be a process id, not a name. MPRIS bus names and X11 window
classes are different namespaces for the same machine: this browser publishes
``org.mpris.MediaPlayer2.chromium`` while setting ``WM_CLASS`` to
``Google-chrome``. Comparing those as strings never matches, and the first build
did exactly that, so every full-screen video in the focused browser was credited
as background listening -- an exoneration of the precise behaviour this sensor
was written to catch. The pid comes free in the ``busctl list`` table, and Tier 1
compares it against the focused window's ``_NET_WM_PID`` through the process
ancestry, which crosses both namespaces exactly. The application name is kept
only so a human reading the log can see what it was.

Reached through ``busctl`` because ``playerctl`` is not installed on the target
machine and D-Bus is. A subprocess also takes a timeout the poll loop can rely
on, which an in-process bus binding would not without more machinery than two
string reads deserve.
"""

from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from lifewatch.models import Observation

# A reader answers with one (bus service name, raw PlaybackStatus, pid) triple
# per player currently on the session bus. An empty list means no player exists,
# which is a different fact from a player that is stopped. The pid may be None
# when the bus did not report one; Tier 1 then declines to attribute.
MediaReader = Callable[[], List[Tuple[str, str, Optional[int]]]]

# The four words this sensor is allowed to record. Nothing a player says
# reaches an observation; it selects one of these or it is ignored.
PLAYING = "playing"
PAUSED = "paused"
STOPPED = "stopped"
NONE = "none"

# Loudest first. One player playing outranks any number of paused ones: the
# question the classifier asks is whether sound is coming out of the machine.
_PRIORITY = (PLAYING, PAUSED, STOPPED)

_STATUS_WORDS = {"playing": PLAYING, "paused": PAUSED, "stopped": STOPPED}

_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_MPRIS_OBJECT = "/org/mpris/MediaPlayer2"
_PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
_STATUS_PROPERTY = "PlaybackStatus"

# Cap on any single busctl call.
_BUSCTL_TIMEOUT_S = 5

# Cap on a whole poll, across every player. The per-call timeout is not on its
# own a bound on this sensor: N unresponsive players cost N timeouts, and
# Runner.tick() is a serial loop, so every other sensor waits behind them.
# Measured before this bound existed, three wedged players stalled a single tick
# for 36 seconds against a 15 second poll interval.
_TOTAL_POLL_BUDGET_S = 6.0


class _PollBudget:
    """A shrinking deadline shared across the calls in one poll.

    Uses ``time.monotonic``, which measures elapsed duration rather than
    answering what time it is. The injected Clock exists so classification and
    escalation can be replayed deterministically; a subprocess timeout is
    neither, and must not move when a FakeClock does.
    """

    def __init__(self, total_s: float) -> None:
        self._deadline = time.monotonic() + total_s

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def exhausted(self) -> bool:
        return self.remaining() <= 0.0


# busctl prints a property as `s "Playing"`: a type code, then the value.
_STATUS_LINE = re.compile(r'^\s*s\s+"(?P<status>[^"]*)"\s*$', re.MULTILINE)


def _busctl(
    args: List[str],
    tolerate_failure: bool = False,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """Run busctl and return stdout, or ``None`` when a tolerated call failed.

    A missing binary or an unreachable session bus raises, which is how
    ``available()`` learns this machine cannot answer the question at all.
    """
    result = subprocess.run(
        ["busctl", *args],
        capture_output=True,
        text=True,
        timeout=min(_BUSCTL_TIMEOUT_S, timeout) if timeout is not None else _BUSCTL_TIMEOUT_S,
    )
    if result.returncode != 0:
        if tolerate_failure:
            return None
        raise OSError(
            f"busctl {' '.join(args)} failed: {result.stderr.strip() or 'no output'}"
        )
    return result.stdout


def _mpris_services(listing: str) -> List[Tuple[str, Optional[int]]]:
    """The MPRIS bus names in a ``busctl list`` table, with their process ids.

    Columns are NAME, PID, PROCESS, ... so the pid comes free with the listing
    already being read. That matters: process identity is the only thing that
    reliably answers "is the player the focused window", because MPRIS bus names
    and X11 window classes are different namespaces for the same machine (the
    browser here publishes "chromium" and sets WM_CLASS "Google-chrome"). Taking
    the pid from this table costs no extra round trip to a bus that may be slow.

    A missing or non-numeric pid yields ``None`` rather than an exception; the
    classifier declines to attribute rather than guessing.
    """
    services: List[Tuple[str, Optional[int]]] = []
    for line in listing.splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith(_MPRIS_PREFIX):
            continue
        pid: Optional[int] = None
        if len(fields) > 1:
            try:
                parsed = int(fields[1])
            except ValueError:
                parsed = 0
            pid = parsed if parsed > 0 else None
        services.append((fields[0], pid))
    return services


def _playback_status(output: str) -> Optional[str]:
    """The quoted word from a property read, or ``None`` if it is not there.

    ``None`` rather than an exception: a player free to answer anything is
    free to answer nothing recognisable, and one confused player must not stop
    the others from being read.
    """
    match = _STATUS_LINE.search(output)
    return match.group("status") if match else None


def read_media_state() -> List[Tuple[str, str, Optional[int]]]:
    """Ask every MPRIS player on the session bus for its playback status.

    Two calls per player at most, and only ``PlaybackStatus`` is ever named.

    A player that disappears between the listing and the property read is
    skipped: quitting mid-poll is a race, not a broken bus, and the correct
    reading of a player that no longer exists is that it is not playing.
    """
    listing = _busctl(["--user", "list", "--acquired", "--no-legend"]) or ""
    players = []
    budget = _PollBudget(_TOTAL_POLL_BUDGET_S)
    for service, pid in _mpris_services(listing):
        # Every call is capped, but so is the whole poll. The per-call timeout
        # alone is not a bound on the sensor: N wedged players cost N timeouts,
        # and Runner.tick() is a serial loop, so the window and idle sensors
        # would sit behind them. Measured at the original settings, three
        # unresponsive players stalled the whole tick for 36 seconds against a
        # 15 second poll interval.
        if budget.exhausted():
            break
        output = _busctl(
            [
                "--user",
                "get-property",
                service,
                _MPRIS_OBJECT,
                _PLAYER_INTERFACE,
                _STATUS_PROPERTY,
            ],
            tolerate_failure=True,
            timeout=budget.remaining(),
        )
        if output is None:
            continue
        status = _playback_status(output)
        if status is not None:
            players.append((service, status, pid))
    return players


def _application_of(service: str) -> str:
    """The application name inside an MPRIS bus name.

    ``org.mpris.MediaPlayer2.testplayer.instance101`` is one window of an
    application that may have several, and the instance number identifies the
    window rather than the program. Tier 1 matches this against a window class,
    which is per-application, so the suffix is dropped.
    """
    if not service.startswith(_MPRIS_PREFIX):
        return ""
    return service[len(_MPRIS_PREFIX) :].split(".", 1)[0]


class MediaSensor:
    """Reports one of ``playing``, ``paused``, ``stopped`` or ``none``.

    ``none`` is a positive statement that no player exists, which is not the
    same as ``stopped`` and is worth the row: the classifier reads the absence
    of a media observation as "unknown", so a sensor that stayed silent when
    nothing was running would leave every quiet hour ambiguous.

    Whether ambience is credit or cover is a judgment, and judgments live in
    the classifier. This sensor holds no threshold and no opinion.
    """

    name = "media"

    def __init__(
        self,
        reader: MediaReader = read_media_state,
        poll_interval_s: int = 15,
    ) -> None:
        self.reader = reader
        # Matched to the window sensor deliberately. A background-media verdict
        # is a statement about two facts at one moment, and sampling ambience
        # more slowly than focus would pair each reading with a stale window.
        self.poll_interval_s = poll_interval_s

    def available(self) -> bool:
        try:
            self.reader()
        except Exception:
            return False
        return True

    def poll(self, now: datetime) -> list[Observation]:
        """Read once. Exceptions travel up to the runner, which isolates them."""
        players = [
            (service, _STATUS_WORDS.get(status.strip().lower(), STOPPED), pid)
            for service, status, pid in self.reader()
        ]
        # An unrecognised status becomes STOPPED above rather than being
        # dropped: a player answering something new still exists, and the one
        # reading it must never be promoted to is "playing".
        value = self._loudest(players)
        return [
            Observation(
                ts=now,
                sensor=self.name,
                kind="media",
                value=value,
                meta=self._attribution(players, value),
            )
        ]

    @staticmethod
    def _loudest(players: List[Tuple[str, str]]) -> str:
        states = {state for _service, state, _pid in players}
        for candidate in _PRIORITY:
            if candidate in states:
                return candidate
        return NONE

    @staticmethod
    def _attribution(players: List[Tuple[str, str, Optional[int]]], value: str) -> dict:
        """Identify the process behind the reported state, when one fits.

        The pid is what matters and the app name is recorded only so a human
        reading the log can tell what it was. Tier 1 compares process identity,
        because MPRIS bus names and X11 window classes are different namespaces:
        comparing them as strings never matched, and the effect was that every
        full-screen video in the focused browser was credited as background
        listening.

        Two different processes in the same state cannot be attributed to
        either, so nothing is claimed, and Tier 1 declines rather than crediting
        an unproven ambient reading.

        No track title, artist, or URL is read here. MPRIS exposes all of them
        on the same interface. The question is whether something is playing, and
        the identity of what is playing does not help answer it.
        """
        if value == NONE:
            return {}
        matching = [
            (service, pid) for service, state, pid in players if state == value
        ]
        pids = {pid for _service, pid in matching if pid is not None}
        if len(pids) != 1:
            return {}
        apps = {_application_of(service) for service, _pid in matching}
        apps.discard("")
        attribution: dict = {"pid": pids.pop()}
        if len(apps) == 1:
            attribution["app"] = apps.pop()
        return attribution
