"""Which known place this is.

Location is inferred from the wireless network name alone: no GPS, no
coordinates, no third-party location service, no battery cost. A place is
whatever the user named while standing in it, so nothing is shipped and nothing
can be guessed about a person from the source.

Known limitation for Stage 1: a machine on wired ethernet reads as offline,
because ``iwgetid`` answers only for wireless interfaces.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from typing import Callable, Optional

from lifewatch.config import Config
from lifewatch.models import Observation

SsidReader = Callable[[], Optional[str]]

# The place question is asked once a minute; five seconds is generous.
_IWGETID_TIMEOUT_S = 5

# iwgetid is an administrative tool and ships in sbin, which is not on the PATH
# of a systemd user service. Without this the sensor would work when started
# from a shell and silently report itself unavailable once installed properly,
# which is the worst of the two failures.
_SBIN_FALLBACKS = ("/usr/sbin/iwgetid", "/sbin/iwgetid")

OFFLINE = "offline"
UNKNOWN = "unknown"


def _iwgetid_path() -> str:
    """Where to find iwgetid, PATH first and sbin second.

    Falls back to the bare name when nothing is found, so a machine genuinely
    without the tool raises ``FileNotFoundError`` -- the signal ``available()``
    reads as "this machine cannot answer the place question".
    """
    found = shutil.which("iwgetid")
    if found:
        return found
    for candidate in _SBIN_FALLBACKS:
        if os.access(candidate, os.X_OK):
            return candidate
    return "iwgetid"


def read_ssid() -> Optional[str]:
    """The current SSID, or ``None`` when there is no wireless association.

    A missing ``iwgetid`` raises ``FileNotFoundError``, which is how
    ``available()`` learns this machine cannot answer the question. Being
    offline is not that: it exits non-zero with no output, and the sensor
    reports it as a place.
    """
    result = subprocess.run(
        [_iwgetid_path(), "-r"],
        capture_output=True,
        text=True,
        timeout=_IWGETID_TIMEOUT_S,
    )
    return result.stdout.strip() or None


class NetworkSensor:
    """Reports the learned place name, ``unknown``, or ``offline``.

    The network name itself is never written to an observation. The place is the
    fact the rest of the system needs; the SSID is only how it was recognised,
    it identifies a physical location, and the observation log is the artefact
    most likely to be handed to someone else while debugging.

    The config is held by reference, not copied, so places learned by a re-run
    of the wizard take effect without restarting the sensor.
    """

    name = "network"

    def __init__(
        self,
        config: Config,
        reader: SsidReader = read_ssid,
        poll_interval_s: int = 60,
    ) -> None:
        self.config = config
        self.reader = reader
        self.poll_interval_s = poll_interval_s

    def available(self) -> bool:
        try:
            self.reader()
        except Exception:
            return False
        return True

    def poll(self, now: datetime) -> list[Observation]:
        ssid = self.reader()
        if ssid is None:
            place = OFFLINE
        else:
            place = self.config.place_for_ssid(ssid) or UNKNOWN
        return [
            Observation(
                ts=now,
                sensor=self.name,
                kind="place",
                value=place,
                meta={},
            )
        ]
