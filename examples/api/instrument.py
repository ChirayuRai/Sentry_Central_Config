import os

import sentry_sdk

from sentry_bridge import get_sentry_options  # written by generate.py in CI


def before_send(event, hint):
    return event


sentry_sdk.init(
    **get_sentry_options(
        "api",
        os.environ.get("APP_ENV", "development"),
        os.environ.get("SENTRY_DSN"),
        # Optional: callbacks and anything else that can't be written as data.
        before_send=before_send,
    )
)
