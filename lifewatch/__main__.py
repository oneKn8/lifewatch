"""Start the instrument: sensors in a background thread, views on a port.

One process, because the parts are useless apart. A sensor loop with nothing
reading it records a term nobody sees, and a wall with no sensor loop is a
dashboard, which is the thing that already failed.

Bind defaults to 0.0.0.0 so the phone on the same network can reach it. That is
a deliberate choice with a real consequence: anyone on your LAN can read the
page. It is stated here rather than buried, and --host 127.0.0.1 makes it local
only at the cost of the phone view, which is most of the point.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

import uvicorn

from lifewatch.classify import Classifier
from lifewatch.classify.tier3 import AskQueue
from lifewatch.clock import SystemClock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.sensors import default_sensors
from lifewatch.sensors.runner import Runner
from lifewatch.store import Store
from lifewatch.watcher import Watcher
from lifewatch.web.app import create_app

logger = logging.getLogger("lifewatch")

DEFAULT_CONFIG = Path("config/config.yaml")
DEFAULT_DB = Path("data/lifewatch.db")
TICK_SECONDS = 15


def sensor_loop(runner: Runner, stop: threading.Event) -> None:
    """Poll forever, and never die of one bad tick.

    A sensor loop that exits on an exception takes the instrument down silently,
    which is the one failure mode worse than a wrong reading: the wall keeps
    showing the last good numbers and nobody learns that nothing is being
    recorded.
    """
    while not stop.is_set():
        try:
            runner.tick()
        except Exception:
            logger.exception("sensor tick failed; continuing")
        stop.wait(TICK_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(prog="lifewatch")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--no-sensors", action="store_true",
                        help="serve the views without polling; useful for design work")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.config.exists():
        # First run serves setup instead of refusing to start. Telling someone
        # to "run the wizard first" and then having no wizard to run is how a
        # tool ships unusable; the missing config IS the instruction.
        from lifewatch.web.setup import create_setup_app

        logger.info("no config at %s", args.config)
        logger.info("setup http://%s:%s/", args.host, args.port)
        logger.info("run the places step while you are actually in each place")
        uvicorn.run(
            create_setup_app(args.config),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
        return

    config = Config.load(args.config)
    clock = SystemClock()
    store = Store(args.db, clock)
    contract = Contract(config, clock)
    ask_queue = AskQueue(store)
    classifier = Classifier(config, ask_queue)
    watcher = Watcher(contract, store, config, clock)

    stop = threading.Event()
    if not args.no_sensors:
        runner = Runner(default_sensors(config), store, clock)
        threading.Thread(
            target=sensor_loop, args=(runner, stop), name="sensors", daemon=True
        ).start()
        logger.info("sensors polling every %ss", TICK_SECONDS)

    app = create_app(contract, store, watcher, ask_queue, config, clock)
    logger.info("wall  http://%s:%s/wall", args.host, args.port)
    logger.info("phone http://%s:%s/", args.host, args.port)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
