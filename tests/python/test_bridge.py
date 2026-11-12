"""Tests for the Python bridge, run against the installed sentry-sdk.

The bridge is loaded the way a project gets it: a copy sitting next to a
sentry_local_config.json snapshot. Snapshots come from tests/fixtures/expected/.
"""
import copy
import logging

import pytest
import sentry_sdk
from sentry_sdk.integrations.argv import ArgvIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from conftest import DSN, RELEASE, golden


class CaptureTransport(Transport):
    """Records what the SDK would send instead of sending it."""

    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def payloads(self, item_type):
        return [item.payload.json for envelope in self.envelopes
                for item in envelope.items if item.type == item_type]


def snapshot_with(project, env, **changes):
    """A golden snapshot with some values in one environment changed."""
    snapshot = copy.deepcopy(golden(project))
    snapshot["environments"][env].update(changes)
    return snapshot


def init(bridge, project, env, **code_options):
    transport = CaptureTransport()
    sentry_sdk.init(**bridge.get_sentry_options(project, env, DSN, transport=transport, **code_options))
    return transport


# --- What the bridge returns -------------------------------------------------

def test_returns_the_options_for_the_environment(load_bridge):
    options = load_bridge("api").get_sentry_options("api", "staging", DSN)
    assert options["traces_sample_rate"] == 0.5
    assert options["max_breadcrumbs"] == 20
    assert options["release"] == RELEASE
    assert options["dsn"] == DSN
    assert options["environment"] == "staging"


def test_regex_entries_become_pattern_strings(load_bridge):
    snapshot = snapshot_with("api", "production", trace_propagation_targets=[{"regex": "^https://x"}, "plain"])
    options = load_bridge(snapshot).get_sentry_options("api", "production", DSN)
    assert options["trace_propagation_targets"] == ["^https://x", "plain"]


def test_builds_configured_integrations_and_lists_disabled_ones(load_bridge):
    options = load_bridge("api").get_sentry_options("api", "production", DSN)
    assert [type(i) for i in options["integrations"]] == [LoggingIntegration]
    assert options["disabled_integrations"] == [DedupeIntegration]


def test_integration_with_no_options_uses_its_defaults(load_bridge):
    snapshot = snapshot_with("worker", "production", integrations={"argv.ArgvIntegration": None})
    options = load_bridge(snapshot).get_sentry_options("worker", "production", DSN)
    assert [type(i) for i in options["integrations"]] == [ArgvIntegration]


def test_unavailable_integrations_are_skipped_with_a_warning(load_bridge, caplog):
    snapshot = snapshot_with("worker", "production", integrations={
        "django.DjangoIntegration": {},   # framework not installed in this test environment
        "made_up.MadeUpIntegration": {},
    })
    options = load_bridge(snapshot).get_sentry_options("worker", "production", DSN)
    assert options["integrations"] == []
    assert "django.DjangoIntegration" in caplog.text
    assert "made_up.MadeUpIntegration" in caplog.text


def test_unknown_environment_warns_and_leaves_sdk_defaults(load_bridge, caplog):
    options = load_bridge("api").get_sentry_options("api", "qa", DSN)
    assert "No Sentry config for environment 'qa'" in caplog.text
    assert options == {"dsn": DSN, "environment": "qa", "integrations": [], "disabled_integrations": []}


def test_wrong_project_name_warns(load_bridge, caplog):
    load_bridge("api").get_sentry_options("billing", "production", DSN)
    assert "Sentry config is for 'api', not 'billing'" in caplog.text


def test_code_options_are_added(load_bridge):
    def before_send(event, hint):
        return event

    options = load_bridge("api").get_sentry_options("api", "production", DSN, before_send=before_send)
    assert options["before_send"] is before_send


def test_code_options_override_the_config_with_a_warning(load_bridge, caplog):
    options = load_bridge("api").get_sentry_options("api", "production", DSN, max_breadcrumbs=5)
    assert options["max_breadcrumbs"] == 5
    assert "Code overrides central Sentry config for 'max_breadcrumbs'" in caplog.text


def test_code_integrations_are_appended(load_bridge):
    options = load_bridge("api").get_sentry_options("api", "production", DSN, integrations=[ArgvIntegration()])
    assert [type(i) for i in options["integrations"]] == [LoggingIntegration, ArgvIntegration]


def test_options_the_sdk_lacks_are_dropped_with_a_warning(load_bridge, caplog):
    snapshot = snapshot_with("api", "production", made_up_option=True)
    options = load_bridge(snapshot).get_sentry_options("api", "production", DSN)
    assert "made_up_option" not in options
    assert "doesn't support 'made_up_option'" in caplog.text
    sentry_sdk.init(**options)  # would raise TypeError if the option got through


# --- What the SDK ends up with -----------------------------------------------

@pytest.mark.parametrize("project", ["api", "worker", "django-app"])
@pytest.mark.parametrize("env", ["production", "staging"])
def test_sdk_accepts_every_snapshot(load_bridge, project, env):
    sentry_sdk.init(**load_bridge(project).get_sentry_options(project, env, DSN))
    client_options = sentry_sdk.get_client().options
    for key, value in golden(project)["environments"][env].items():
        if key != "integrations":
            assert client_options[key] == value, key


def test_disabled_integration_is_not_installed(load_bridge):
    init(load_bridge("api"), "api", "production")
    installed = sentry_sdk.get_client().integrations
    assert "logging" in installed
    assert "dedupe" not in installed


# --- What actually gets sent -------------------------------------------------

def test_errors_carry_the_release_and_environment(load_bridge):
    sent = init(load_bridge("api"), "api", "staging")
    sentry_sdk.capture_exception(ValueError("boom"))
    [event] = sent.payloads("event")
    assert event["release"] == RELEASE
    assert event["environment"] == "staging"


def test_ignored_errors_are_not_sent(load_bridge):
    sent = init(load_bridge("api"), "api", "staging")
    sentry_sdk.capture_exception(KeyboardInterrupt())              # from default
    sentry_sdk.capture_exception(ConnectionResetError("reset"))    # added by the project
    sentry_sdk.capture_exception(ValueError("real bug"))
    [event] = sent.payloads("event")
    assert event["exception"]["values"][0]["type"] == "ValueError"


def test_error_sample_rate_of_zero_sends_no_errors(load_bridge):
    sent = init(load_bridge(snapshot_with("worker", "staging", sample_rate=0)), "worker", "staging")
    sentry_sdk.capture_exception(ValueError("boom"))
    assert sent.payloads("event") == []


def test_traces_sample_rate_of_one_sends_every_transaction(load_bridge):
    sent = init(load_bridge("worker"), "worker", "staging")
    for i in range(5):
        with sentry_sdk.start_transaction(name=f"job-{i}"):
            pass
    assert len(sent.payloads("transaction")) == 5


def test_traces_sample_rate_of_zero_sends_no_transactions(load_bridge):
    sent = init(load_bridge(snapshot_with("worker", "staging", traces_sample_rate=0)), "worker", "staging")
    for i in range(5):
        with sentry_sdk.start_transaction(name=f"job-{i}"):
            pass
    assert sent.payloads("transaction") == []


def test_configured_integration_options_take_effect(load_bridge):
    # The config sets LoggingIntegration(event_level=40): errors become events, warnings don't.
    sent = init(load_bridge("api"), "api", "staging")
    logging.getLogger("bridge-test").warning("just a warning")
    logging.getLogger("bridge-test").error("an error")
    assert [e["logentry"]["message"] for e in sent.payloads("event")] == ["an error"]


def test_integration_passed_in_code_wins_over_the_config(load_bridge):
    sent = init(load_bridge("api"), "api", "staging",
                integrations=[LoggingIntegration(event_level=logging.CRITICAL)])
    logging.getLogger("bridge-test").error("an error")
    assert sent.payloads("event") == []


def test_before_send_from_code_runs(load_bridge):
    def before_send(event, hint):
        event.setdefault("tags", {})["checked"] = "yes"
        return event

    sent = init(load_bridge("api"), "api", "staging", before_send=before_send)
    sentry_sdk.capture_exception(ValueError("boom"))
    [event] = sent.payloads("event")
    assert event["tags"]["checked"] == "yes"
