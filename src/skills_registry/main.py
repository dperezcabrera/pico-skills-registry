"""Import-safe entry point: nothing starts at module level.

    uvicorn --factory skills_registry.main:create_app

Config values support ${VAR:default} placeholders resolved from the
environment at boot.
"""

import os
import re

import yaml
from fastapi import FastAPI
from pico_ioc import DictSource, configuration, init

_PLACEHOLDER = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def _expand(value):
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def create_app() -> FastAPI:
    config_path = os.environ.get("CONFIG_PATH", "config/application.yaml")
    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    container = init(
        modules=[
            "skills_registry",
            "pico_fastapi",
            "pico_sqlalchemy",
            "pico_server_auth",
            "pico_client_auth",
            "pico_actuator",
        ],
        config=configuration(DictSource(_expand(raw))),
    )
    return container.get(FastAPI)
