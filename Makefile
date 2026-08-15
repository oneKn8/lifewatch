# This host sources ROS 2 globally, which sets PYTHONPATH to ROS site-packages and
# leaks ROS pytest plugins into any virtualenv. Bare `pytest` fails on an unrelated
# `lark` import before it ever reaches this project's code. Every target below runs
# with a scrubbed environment so that trap cannot reach a contributor or a CI job.

PY := .venv/bin/python
CLEAN_ENV := env -u PYTHONPATH -u AMENT_PREFIX_PATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

.PHONY: help venv test watch run wall lint clean

help:
	@echo "make venv   - create .venv and install the project in editable mode"
	@echo "make test   - run the suite in a scrubbed environment"
	@echo "make run    - start the sensor runner and web server"
	@echo "make wall   - open the wall view full screen on the external display"
	@echo "make clean  - remove build artefacts and caches"

venv:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test:
	$(CLEAN_ENV) $(PY) -m pytest

run:
	$(CLEAN_ENV) $(PY) -m lifewatch

wall:
	$(CLEAN_ENV) $(PY) -m lifewatch.wall_launcher

clean:
	rm -rf .pytest_cache **/__pycache__ *.egg-info build dist
