# Copied in by CI. Do not edit in the project.
import importlib
import json
import logging
from pathlib import Path

from sentry_sdk.consts import DEFAULT_OPTIONS

SNAPSHOT = json.loads((Path(__file__).parent / "sentry_config.json").read_text())
logger = logging.getLogger(__name__)


def _revive(value):
    # { regex: "..." } -> plain string; Python already treats these strings as patterns.
    if isinstance(value, list):
        return [_revive(v) for v in value]
    if isinstance(value, dict):
        if value.keys() == {"regex"}:
            return value["regex"]
        return {k: _revive(v) for k, v in value.items()}
    return value


def _load_integration(path):
    module, _, cls = path.rpartition(".")
    return getattr(importlib.import_module(f"sentry_sdk.integrations.{module}"), cls)


def _build_integrations(config):
    enabled, disabled = [], []
    for path, options in (config or {}).items():
        try:
            cls = _load_integration(path)
        except Exception as exc:  # missing module, or framework not installed
            logger.warning("Skipping Sentry integration %r: %s", path, exc)
            continue
        if options is False:
            disabled.append(cls)
        else:
            enabled.append(cls(**(options if isinstance(options, dict) else {})))
    return enabled, disabled


def get_sentry_options(project, environment, dsn, **code_options):
    """code_options: callbacks and anything else that can't live in YAML,
    e.g. before_send=..., integrations=[...]"""
    if project != SNAPSHOT["project"]:
        logger.warning("Sentry config is for %r, not %r", SNAPSHOT["project"], project)
    resolved = SNAPSHOT["environments"].get(environment)
    if resolved is None:
        logger.warning("No Sentry config for environment %r", environment)

    options = _revive(resolved or {})
    enabled, disabled = _build_integrations(options.pop("integrations", None))

    code_integrations = code_options.pop("integrations", [])
    for key in code_options.keys() & options.keys():
        logger.warning("Code overrides central Sentry config for %r", key)

    options = {**options, **code_options}
    # init() raises TypeError on unknown options; drop any this SDK version lacks.
    for key in options.keys() - DEFAULT_OPTIONS.keys():
        logger.warning("Sentry SDK doesn't support %r, skipping", key)
        del options[key]

    return {
        **options,
        "dsn": dsn,
        "environment": environment,
        "integrations": enabled + list(code_integrations),
        "disabled_integrations": disabled,
    }
