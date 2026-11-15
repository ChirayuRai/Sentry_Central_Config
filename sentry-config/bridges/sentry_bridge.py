# Copied in by CI. Do not edit in the project.
import importlib
import json
import logging
from pathlib import Path

from sentry_sdk.consts import DEFAULT_OPTIONS

SNAPSHOT = json.loads((Path(__file__).parent / "sentry_local_config.json").read_text())
logger = logging.getLogger(__name__)


def _revive(value):
    # { regex: "..." } -> its pattern string. Python options that take patterns, like
    # trace_propagation_targets, read plain strings as regular expressions already.
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


def _build_integrations(config, from_code):
    in_code = {integration.identifier for integration in from_code}
    enabled, disabled = [], []
    for path, options in (config or {}).items():
        try:
            cls = _load_integration(path)
            if options is False:
                disabled.append(cls)
            elif cls.identifier in in_code:
                logger.warning("Code replaces the configured Sentry integration %r", path)
            else:
                enabled.append(cls(**(options if isinstance(options, dict) else {})))
        except Exception as exc:  # missing module, framework not installed, or a bad option
            logger.warning("Skipping Sentry integration %r: %s", path, exc)
    return enabled, disabled


def get_sentry_options(project, environment, dsn, **code_options):
    """code_options: callbacks and anything else that can't be written as data,
    e.g. before_send=..., integrations=[...]"""
    if project != SNAPSHOT["project"]:
        logger.warning("Sentry config is for %r, not %r", SNAPSHOT["project"], project)
    resolved = SNAPSHOT["environments"].get(environment)
    if resolved is None:
        logger.warning("No Sentry config for environment %r", environment)

    options = _revive(resolved or {})
    code_integrations = list(code_options.pop("integrations", []))
    enabled, disabled = _build_integrations(options.pop("integrations", None), code_integrations)

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
        "integrations": enabled + code_integrations,
        "disabled_integrations": disabled,
    }
