"""Runtime configuration.

Everything that describes a particular person lives here and nowhere else: their
places, their commitments, how hard they have asked to be pushed, and why they
are doing any of it. The engine holds no domain knowledge and no user data, so
this file's job is to be the only thing that does.

Places are *learned*, never defaulted. There is no shipped SSID and no shipped
location, because a value that ships is a value that can be committed by
accident. The wizard captures whatever the machine can currently see and names
it. See ``docs/superpowers/specs/2026-08-15-lifewatch-design.md`` section 10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LADDER: list[dict[str, Any]] = [
    {"rung": 1, "after_minutes": 0, "effector": "wall", "requires_response": False},
    {"rung": 2, "after_minutes": 5, "effector": "notify", "requires_response": False},
]


@dataclass
class Place:
    """A named location, recognised by some matcher the sensors can evaluate."""

    name: str
    matcher_type: str
    matcher_value: str


@dataclass
class Config:
    places: dict[str, Place] = field(default_factory=dict)
    commitments: list[dict[str, Any]] = field(default_factory=list)
    ladder: list[dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_LADDER))
    passes_per_week: int = 1
    accounted_places: list[str] = field(default_factory=list)
    idle_threshold_s: int = 900
    classifier: dict[str, Any] = field(default_factory=dict)
    notify_url: str | None = None
    consequence_chain: list[str] = field(default_factory=list)
    pack: str = "school"

    @classmethod
    def empty(cls) -> "Config":
        return cls()

    # -- places -----------------------------------------------------------

    def learn_place(self, name: str, ssid: str) -> Place:
        """Record that ``name`` is wherever this SSID is.

        Replaces any previous matcher for the same name, so re-running the
        wizard after moving house does the obvious thing.
        """
        place = Place(name=name, matcher_type="ssid", matcher_value=ssid)
        self.places[name] = place
        return place

    def place_for_ssid(self, ssid: str) -> str | None:
        for name, place in self.places.items():
            if place.matcher_type == "ssid" and place.matcher_value == ssid:
                return name
        return None

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False))

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "places": {
                name: {
                    "matcher_type": place.matcher_type,
                    "matcher_value": place.matcher_value,
                }
                for name, place in self.places.items()
            },
            "commitments": self.commitments,
            "ladder": self.ladder,
            "passes_per_week": self.passes_per_week,
            "accounted_places": self.accounted_places,
            "idle_threshold_s": self.idle_threshold_s,
            "classifier": self.classifier,
            "notify_url": self.notify_url,
            "consequence_chain": self.consequence_chain,
            "pack": self.pack,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        places = {
            name: Place(
                name=name,
                matcher_type=body.get("matcher_type", "ssid"),
                matcher_value=body.get("matcher_value", ""),
            )
            for name, body in (raw.get("places") or {}).items()
        }
        return cls(
            places=places,
            commitments=raw.get("commitments") or [],
            ladder=raw.get("ladder") or list(DEFAULT_LADDER),
            passes_per_week=raw.get("passes_per_week", 1),
            accounted_places=raw.get("accounted_places") or [],
            idle_threshold_s=raw.get("idle_threshold_s", 900),
            classifier=raw.get("classifier") or {},
            notify_url=raw.get("notify_url"),
            consequence_chain=raw.get("consequence_chain") or [],
            pack=raw.get("pack", "school"),
        )
