"""First-run setup.

The wizard is not a convenience feature. It is the structural guarantee that no
personal value ever reaches source: everything a shipped default would otherwise
have to guess -- which network is home, which courses are being taken, how hard
this is allowed to push -- is collected here at runtime instead. Nothing can be
hardcoded if it is only ever learned. See the design spec section 10.

Two refusals carry most of that guarantee, and both are deliberate:

* A place cannot be learned while the machine is offline. An empty matcher does
  not match nothing, it matches everywhere, so a silently learned blank would
  make the network sensor report "home" from anywhere on earth. Failing is the
  safe outcome; the user reconnects and presses the button again.
* ``finish`` will not write a config that still carries a placeholder from the
  shipped example. A placeholder that survives setup is a value nobody chose,
  and the whole point of collecting at runtime is that nobody ships one.

The wizard is re-runnable over an existing config, so learning a place again
after moving house, or correcting a commitment, does the obvious thing.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable

from .config import Config, Place

#: Marker used by ``config.example.yaml`` for values the user must supply.
PLACEHOLDER_MARKER = "CHANGE-ME"

SsidReader = Callable[[], str | None]


class Wizard:
    """Collects a runnable config from a live machine and a live user.

    ``ssid_reader`` is injected rather than imported so setup can be tested
    without a radio, and so a contributor on a platform where SSIDs are read
    some other way replaces one callable instead of the wizard.
    """

    def __init__(self, config: Config, ssid_reader: SsidReader) -> None:
        self.config = config
        self.ssid_reader = ssid_reader

    # -- places -----------------------------------------------------------

    def learn_place_now(self, name: str) -> Place:
        """Name wherever this machine currently is.

        The user presses "I am home now" and the SSID in front of the machine
        becomes the matcher for ``name``. Nothing is offered as a default and
        nothing is typed in, because a value the user can type is a value the
        user can type into a pull request.

        Raises ``ValueError`` when no network is visible. That includes the OS
        refusing to answer at all: a machine that cannot say which network it is
        on is, for this purpose, the same as a machine that is on none.
        """
        if not name or not name.strip():
            raise ValueError("a place needs a name")

        try:
            ssid = self.ssid_reader()
        except OSError as exc:
            raise ValueError(f"no network detected: {exc}") from exc

        if ssid is None or not str(ssid).strip():
            raise ValueError("no network detected")

        return self.config.learn_place(name.strip(), ssid=str(ssid).strip())

    # -- commitments ------------------------------------------------------

    def add_commitment(
        self,
        id: str,
        label: str,
        weekly_target_minutes: int,
        **pack_fields: Any,
    ) -> dict[str, Any]:
        """Declare something the user is committing to, with the pack's fields.

        ``pack_fields`` is passed through untouched and unvalidated. The engine
        must not know what a course code is; validating these here would put the
        pack's schema inside the engine and defeat the split the whole design
        rests on. The pack declares its fields, the setup view renders them, and
        this records whatever comes back.

        Re-adding an existing id replaces it in place rather than appending a
        duplicate, because the wizard is re-runnable and correcting a typo must
        not leave both versions in the contract.
        """
        commitment_id = str(id).strip()
        if not commitment_id:
            raise ValueError("a commitment needs an id")
        if not label or not str(label).strip():
            raise ValueError("a commitment needs a label")

        minutes = int(weekly_target_minutes)
        if minutes <= 0:
            raise ValueError(
                "weekly_target_minutes must be positive; a commitment with no "
                "target is not a commitment"
            )

        commitment: dict[str, Any] = {
            "id": commitment_id,
            "label": str(label).strip(),
            "weekly_target_minutes": minutes,
            **pack_fields,
        }

        for index, existing in enumerate(self.config.commitments):
            if existing.get("id") == commitment_id:
                self.config.commitments[index] = commitment
                break
        else:
            self.config.commitments.append(commitment)

        return commitment

    # -- ladder -----------------------------------------------------------

    def set_ladder(self, rungs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Record how hard the system may push, and how soon.

        Rungs are normalised and stored in firing order, so the watcher can walk
        them forward without re-sorting a user-authored list.

        The effector name is not checked against anything. Which channels exist
        is a property of the build and the machine, discovered at run time, and a
        wizard that rejected a channel this build happens to lack would be wrong
        the moment somebody added one.

        An empty ladder is refused. A system that never escalates is a passive
        dashboard, which is the exact failure this project exists to avoid; the
        way to be left alone is a pass or sick mode, both of which are honest
        about being exceptions.
        """
        normalised: list[dict[str, Any]] = []
        seen: set[int] = set()

        for raw in rungs:
            if not isinstance(raw, Mapping):
                raise ValueError(f"a ladder rung must be a mapping, got {raw!r}")
            for required in ("rung", "after_minutes", "effector"):
                if required not in raw:
                    raise ValueError(f"ladder rung {raw!r} is missing {required!r}")

            number = int(raw["rung"])
            after_minutes = int(raw["after_minutes"])
            effector = str(raw["effector"]).strip()

            if number < 1:
                raise ValueError(f"ladder rung numbers start at 1, got {number}")
            if after_minutes < 0:
                raise ValueError(
                    f"rung {number} fires {after_minutes} minutes in, which is "
                    "before the block it is watching"
                )
            if not effector:
                raise ValueError(f"rung {number} has no effector")
            if number in seen:
                raise ValueError(f"rung {number} is declared twice")
            seen.add(number)

            normalised.append(
                {
                    "rung": number,
                    "after_minutes": after_minutes,
                    "effector": effector,
                    "requires_response": bool(raw.get("requires_response", False)),
                }
            )

        if not normalised:
            raise ValueError(
                "an empty ladder never escalates, which is a passive dashboard; "
                "use a pass or sick mode to be left alone"
            )

        normalised.sort(key=lambda rung: (rung["after_minutes"], rung["rung"]))
        self.config.ladder = normalised
        return normalised

    # -- finishing --------------------------------------------------------

    def finish(self, path: Path) -> Path:
        """Validate what was collected and write it, or refuse and write nothing.

        The checks are the wizard's promise made enforceable. A surviving
        ``CHANGE-ME`` means a step was skipped, and a blank matcher means a place
        that matches everywhere; either would produce a system that looks
        configured and behaves at random.

        The file is made owner-only *before* it is written, not after. It is the
        one file in the project that contains anything personal -- places,
        courses, the consequence chain, a push URL -- and writing it first and
        tightening it afterwards leaves a window in which every other account on
        the machine can read the lot.
        """
        problems = self._problems()
        if problems:
            raise ValueError("setup is not finished: " + "; ".join(problems))

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
            os.chmod(path, 0o600)
        except OSError:
            # Permissions are hardening, not correctness. A filesystem that
            # cannot express them must not cost the user their config.
            pass
        self.config.save(path)
        return path

    def _problems(self) -> list[str]:
        problems: list[str] = []

        for name, place in self.config.places.items():
            if not place.matcher_value or not place.matcher_value.strip():
                problems.append(
                    f"place {name!r} has an empty matcher, which would match everywhere"
                )

        for location in _placeholder_paths(self.config.to_dict()):
            problems.append(f"{location} still holds a {PLACEHOLDER_MARKER} placeholder")

        return problems


def _placeholder_paths(node: Any, prefix: str = "") -> list[str]:
    """Every location in the config whose value is still a shipped placeholder."""
    found: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            found.extend(_placeholder_paths(value, child))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(_placeholder_paths(value, f"{prefix}[{index}]"))
    elif isinstance(node, str) and PLACEHOLDER_MARKER in node.upper():
        found.append(prefix or "config")
    return found
