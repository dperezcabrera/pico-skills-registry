"""Import-safe entry point: nothing starts at module level.

    uvicorn --factory skills_registry.main:create_app

Config values support ${VAR:default} placeholders resolved from the
environment at boot (pico-ioc expand_env).
"""

import os

from fastapi import FastAPI
from pico_ioc import YamlTreeSource, configuration, init


def create_app() -> FastAPI:
    config_path = os.environ.get("CONFIG_PATH", "config/application.yaml")
    container = init(
        modules=[
            "skills_registry",
            "pico_fastapi",
            "pico_sqlalchemy",
            "pico_server_auth",
            "pico_client_auth",
            "pico_actuator",
        ],
        config=configuration(YamlTreeSource(config_path, expand_env=True)),
    )
    return container.get(FastAPI)
