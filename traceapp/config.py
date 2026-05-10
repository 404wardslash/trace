from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def trace_home() -> Path:
    return Path(os.environ.get("TRACE_HOME", "~/.trace")).expanduser()


def db_path() -> Path:
    return trace_home() / "trace.db"


def config_path() -> Path:
    return trace_home() / "config.json"


def ensure_home() -> Path:
    home = trace_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "engagements").mkdir(exist_ok=True)
    return home


def load_config() -> dict[str, Any]:
    ensure_home()
    path = config_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(data: dict[str, Any]) -> None:
    ensure_home()
    config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_active(engagement: str | None = None, target: str | None = None) -> None:
    cfg = load_config()
    if engagement is not None:
        cfg["active_engagement"] = engagement
        cfg.pop("active_target", None)
    if target is not None:
        cfg["active_target"] = target
    save_config(cfg)


def clear_active() -> None:
    cfg = load_config()
    cfg.pop("active_engagement", None)
    cfg.pop("active_target", None)
    save_config(cfg)


def active_engagement_name() -> str | None:
    return load_config().get("active_engagement")


def active_target_name() -> str | None:
    return load_config().get("active_target")


def engagement_dir(slug: str) -> Path:
    root = trace_home() / "engagements" / slug
    (root / "evidence").mkdir(parents=True, exist_ok=True)
    (root / "exports").mkdir(parents=True, exist_ok=True)
    return root
