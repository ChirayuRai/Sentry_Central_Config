"""Checks platforms/python.txt against the installed sentry-sdk.

Fails when the list names an option the installed SDK doesn't have, for example
after an SDK upgrade renames or removes one. Run it against every SDK version
your projects use.
"""
import warnings

from sentry_sdk import VERSION
from sentry_sdk.consts import DEFAULT_OPTIONS

from conftest import CONFIG_DIR

LISTED = set((CONFIG_DIR / "platforms" / "python.txt").read_text().split())


def test_every_listed_option_exists_in_the_installed_sdk():
    missing = sorted(LISTED - DEFAULT_OPTIONS.keys())
    assert not missing, f"sentry-sdk {VERSION} doesn't support: {', '.join(missing)}"


def test_report_sdk_options_missing_from_the_list():
    # Not a failure: new SDK options just can't be set centrally until they're added.
    new = sorted(k for k in DEFAULT_OPTIONS.keys() - LISTED if not k.startswith("_"))
    if new:
        warnings.warn(f"sentry-sdk {VERSION} has options not in python.txt: {', '.join(new)}")
