"""Checks the Python option lists (platforms/python.txt, django.txt) against the installed sentry-sdk.

Fails when a list names an option the installed SDK doesn't have, for example
after an SDK upgrade renames or removes one. Run it against every SDK version
your projects use.
"""
import warnings

import pytest
from sentry_sdk import VERSION
from sentry_sdk.consts import DEFAULT_OPTIONS

import generate
from conftest import CONFIG_DIR

LISTS = [f"{platform}.txt" for platform, bridge in generate.BRIDGES.items() if bridge == "sentry_bridge.py"]


def listed(name):
    return set((CONFIG_DIR / "platforms" / name).read_text().split())


@pytest.mark.parametrize("name", LISTS)
def test_every_listed_option_exists_in_the_installed_sdk(name):
    missing = sorted(listed(name) - DEFAULT_OPTIONS.keys())
    assert not missing, f"sentry-sdk {VERSION} doesn't support these options in {name}: {', '.join(missing)}"


@pytest.mark.parametrize("name", LISTS)
def test_report_sdk_options_missing_from_the_list(name):
    # Not a failure: new SDK options just can't be set centrally until they're added.
    # Options the config can't hold (callbacks, dsn, environment, release) aren't reported.
    not_in_config = generate.CODE_ONLY | generate.CALLER_SET
    new = sorted(k for k in DEFAULT_OPTIONS.keys() - listed(name) - not_in_config if not k.startswith("_"))
    if new:
        warnings.warn(f"sentry-sdk {VERSION} has options not in {name}: {', '.join(new)}")
