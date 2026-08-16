"""First-run setup, served as a page.

This is the mechanism that keeps personal values out of the source tree. Not a
convenience: a structural guarantee. Nothing about a particular person can be
hardcoded if the only way it enters the system is a human typing it at runtime,
and the only way a place is learned is by standing in it and pressing a button.

Kept apart from ``app.py`` because it has the opposite lifetime. The main app
runs all term; this runs once, and refuses to run at all once a config exists
unless it is asked to on purpose.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lifewatch.config import Config
from lifewatch.sensors.network import read_ssid
from lifewatch.wizard import Wizard

STATIC = Path(__file__).parent / "static"


class PlaceRequest(BaseModel):
    name: str


class CommitmentRequest(BaseModel):
    id: str
    label: str
    weekly_target_minutes: int
    fields: dict[str, Any] = {}


class LadderRequest(BaseModel):
    rungs: list[dict[str, Any]]


class SettingsRequest(BaseModel):
    passes_per_week: int | None = None
    idle_threshold_s: int | None = None
    accounted_places: list[str] | None = None
    consequence_chain: list[str] | None = None
    notify_url: str | None = None
    classifier: dict[str, Any] | None = None


def load_pack(name: str) -> dict[str, Any]:
    """The pack's own declaration of what it needs collected.

    Imported by name so a pack a contributor adds is reachable without the
    engine holding a list of packs, which would be domain knowledge in the one
    place that must not have any.
    """
    try:
        module = importlib.import_module(f"packs.{name}")
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no pack named {name!r}") from exc
    loader = getattr(module, "load_pack", None)
    if loader is None:
        raise HTTPException(status_code=500, detail=f"pack {name!r} declares no fields")
    return loader()


def create_setup_app(config_path: Path, ssid_reader=read_ssid) -> FastAPI:
    """A short-lived app whose only job is to write a config and stop."""
    app = FastAPI(title="lifewatch setup", docs_url=None, redoc_url=None)

    state: dict[str, Any] = {
        "wizard": Wizard(Config.empty(), ssid_reader),
        "written": None,
    }

    def wizard() -> Wizard:
        return state["wizard"]

    @app.get("/api/setup/state")
    def setup_state() -> dict[str, Any]:
        config = wizard().config
        # Shown so the user can see what they are about to name, and so a
        # captured value is never a surprise.
        try:
            visible = ssid_reader()
        except Exception:
            visible = None
        return {
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
            "written": state["written"],
            "visible_network": visible,
            "pack": config.pack,
            "pack_fields": load_pack(config.pack).get("commitment_fields", []),
            "places": {
                name: place.matcher_value for name, place in config.places.items()
            },
            "commitments": config.commitments,
            "ladder": config.ladder,
            "passes_per_week": config.passes_per_week,
            "accounted_places": config.accounted_places,
            "consequence_chain": config.consequence_chain,
            "notify_url": config.notify_url,
            "problems": wizard()._problems(),
        }

    @app.post("/api/setup/place")
    def learn_place(body: PlaceRequest) -> dict[str, Any]:
        """Capture whatever network is visible right now and name it.

        Fails loudly when offline rather than learning an empty matcher, which
        would create a place that matches everywhere.
        """
        try:
            place = wizard().learn_place_now(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"name": place.name, "matcher_value": place.matcher_value}

    @app.post("/api/setup/commitment")
    def add_commitment(body: CommitmentRequest) -> dict[str, Any]:
        try:
            return wizard().add_commitment(
                id=body.id,
                label=body.label,
                weekly_target_minutes=body.weekly_target_minutes,
                **body.fields,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/setup/commitment/{commitment_id}")
    def remove_commitment(commitment_id: str) -> dict[str, Any]:
        config = wizard().config
        before = len(config.commitments)
        config.commitments = [c for c in config.commitments if c.get("id") != commitment_id]
        return {"removed": before - len(config.commitments)}

    @app.post("/api/setup/ladder")
    def set_ladder(body: LadderRequest) -> dict[str, Any]:
        try:
            return {"ladder": wizard().set_ladder(body.rungs)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/setup/settings")
    def set_settings(body: SettingsRequest) -> dict[str, Any]:
        config = wizard().config
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(config, field, value)
        return {"ok": True}

    @app.post("/api/setup/finish")
    def finish() -> dict[str, Any]:
        try:
            written = wizard().finish(config_path)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        state["written"] = str(written)
        return {"written": str(written)}

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(STATIC / "setup.html")

    return app
