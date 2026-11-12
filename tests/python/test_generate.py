"""Tests for the CI step (sentry-config/generate.py)."""
import json
import os
import subprocess
import sys

import pytest
import yaml

import generate
from conftest import CONFIG_DIR, EXPECTED, FIXTURE_CONFIG, PROJECTS, RELEASE


def run_generate(cwd, *args, config=FIXTURE_CONFIG):
    return subprocess.run(
        [sys.executable, str(CONFIG_DIR / "generate.py"), "--config", str(config), *args],
        cwd=cwd, capture_output=True, text=True,
    )


def write_config(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def resolved(config, project, env, release=None):
    return generate.resolve(config, project, release)["environments"][env]


# --- Full output -------------------------------------------------------------

@pytest.mark.parametrize("project", PROJECTS)
def test_output_matches_golden_file(project, tmp_path):
    result = run_generate(tmp_path, "--project", project, "--release", RELEASE)
    assert result.returncode == 0, result.stderr

    snapshot = json.loads((tmp_path / "out" / project / "sentry_local_config.json").read_text())
    expected = EXPECTED / f"{project}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        expected.parent.mkdir(exist_ok=True)
        expected.write_text(json.dumps(snapshot, indent=2) + "\n")
    assert snapshot == json.loads(expected.read_text())


@pytest.mark.parametrize("project, bridge, other", [
    ("web-app", "sentry_bridge.js", "sentry_bridge.py"),
    ("api", "sentry_bridge.py", "sentry_bridge.js"),
])
def test_copies_only_the_matching_bridge_unchanged(project, bridge, other, tmp_path):
    assert run_generate(tmp_path, "--project", project).returncode == 0
    out = tmp_path / "out" / project
    assert (out / bridge).read_bytes() == (CONFIG_DIR / "bridges" / bridge).read_bytes()
    assert not (out / other).exists()


def test_release_is_left_out_without_the_flag(config):
    for env in ("production", "staging"):
        assert "release" not in resolved(config, "web-app", env)


def test_release_is_set_in_every_environment(config):
    for env in ("production", "staging"):
        assert resolved(config, "api", env, release="2.0.0")["release"] == "2.0.0"


# --- Merge rules -------------------------------------------------------------

def test_merge_combines_maps_recursively():
    base = {"integrations": {"replay": {"maskAllText": True, "blockAllMedia": True}}}
    override = {"integrations": {"replay": {"maskAllText": False}, "dedupe": False}}
    assert generate.merge(base, override) == {
        "integrations": {"replay": {"maskAllText": False, "blockAllMedia": True}, "dedupe": False},
    }


def test_merge_appends_lists():
    assert generate.merge({"ignoreErrors": ["a"]}, {"ignoreErrors": ["b"]}) == {"ignoreErrors": ["a", "b"]}


def test_merge_replaces_single_values():
    assert generate.merge({"sampleRate": 1.0}, {"sampleRate": 0.5}) == {"sampleRate": 0.5}


def test_merge_lets_false_replace_a_map():
    assert generate.merge({"replay": {"maskAllText": True}}, {"replay": False}) == {"replay": False}


def test_merge_does_not_modify_its_inputs():
    base, override = {"a": {"b": [1]}}, {"a": {"b": [2]}}
    generate.merge(base, override)
    assert base == {"a": {"b": [1]}} and override == {"a": {"b": [2]}}


# --- Resolving a project -----------------------------------------------------

def test_new_project_gets_the_defaults_for_every_environment(config):
    snapshot = generate.resolve(config, "worker", None)
    assert set(snapshot["environments"]) == {"production", "staging"}
    staging = snapshot["environments"]["staging"]
    assert staging["traces_sample_rate"] == 1.0
    assert staging["debug"] is True
    assert staging["max_breadcrumbs"] == 50


def test_environment_override_only_changes_that_environment(config):
    assert resolved(config, "web-app", "production")["tracesSampleRate"] == 0.2
    assert resolved(config, "web-app", "staging")["tracesSampleRate"] == 1.0


def test_project_shared_overrides_default_shared_in_every_environment(config):
    for env in ("production", "staging"):
        assert resolved(config, "api", env)["max_breadcrumbs"] == 20


def test_names_are_converted_for_javascript_and_kept_for_python(config):
    js = resolved(config, "web-app", "production")
    py = resolved(config, "api", "production")
    assert "tracesSampleRate" in js and "traces_sample_rate" not in js
    assert "traces_sample_rate" in py and "tracesSampleRate" not in py


def test_options_a_platform_lacks_are_dropped(config):
    py = resolved(config, "api", "production")
    assert not any(key.startswith("replays") for key in py)


def test_default_native_options_only_reach_their_platform(config):
    js = resolved(config, "web-app", "production")
    py = resolved(config, "api", "production")
    assert "include_local_variables" not in js and "ignore_errors" not in js
    assert "ignoreErrors" not in py and "denyUrls" not in py


def test_project_lists_are_appended_to_the_defaults(config):
    assert resolved(config, "web-app", "production")["ignoreErrors"] == [
        "ResizeObserver loop limit exceeded",
        {"regex": "^Script error\\.?$"},
        "NonErrorPromiseRejection",
    ]
    assert resolved(config, "api", "production")["ignore_errors"] == ["KeyboardInterrupt", "ConnectionResetError"]


def test_project_integration_options_merge_with_the_defaults(config):
    integrations = resolved(config, "web-app", "production")["integrations"]
    assert integrations["replay"] == {"maskAllText": False, "blockAllMedia": True}
    assert integrations["browserTracing"] == {}
    assert integrations["dedupe"] is False


def test_native_options_win_over_shared_ones_at_the_same_level(config):
    # default.environments sets a 0.1 profiling rate; default.native sets 0 for the browser.
    assert resolved(config, "web-app", "production")["profileSessionSampleRate"] == 0


def test_profile_lifecycle_is_set_only_when_profiling_is_on(config):
    assert resolved(config, "api", "production")["profile_lifecycle"] == "trace"
    assert "profile_lifecycle" not in resolved(config, "api", "staging")
    assert "profileLifecycle" not in resolved(config, "web-app", "production")


def test_an_explicit_profile_lifecycle_is_kept(config):
    config["api"]["native"]["profile_lifecycle"] = "manual"
    assert resolved(config, "api", "production")["profile_lifecycle"] == "manual"


def test_a_zero_override_is_kept(config):
    config["web-app"]["environments"]["staging"] = {"traces_sample_rate": 0}
    assert resolved(config, "web-app", "staging")["tracesSampleRate"] == 0


# --- Validation --------------------------------------------------------------

def test_fixture_config_is_valid(config):
    assert generate.validate(config) == []
    for project in PROJECTS:
        errors = []
        generate.check_resolved(generate.resolve(config, project, None), errors)
        assert errors == []


def _set(path, value):
    def mutate(config):
        target = config
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = value
    return mutate


def _delete(*path):
    def mutate(config):
        target = config
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


INVALID = {
    "callback in native": (
        _set(["web-app", "native", "beforeSend"], "dropHealthChecks"),
        "web-app.native.beforeSend: functions and objects must be passed in code"),
    "callback in shared": (
        _set(["default", "shared", "before_send"], "drop"),
        "default.shared.before_send: functions and objects must be passed in code"),
    "sampler": (
        _set(["api", "native", "traces_sampler"], "sampler"),
        "api.native.traces_sampler: functions and objects must be passed in code"),
    "dsn in config": (
        _set(["api", "native", "dsn"], "https://key@o0.ingest.sentry.io/0"),
        "api.native.dsn: set by the bridge call or CI"),
    "environment in config": (
        _set(["default", "shared", "environment"], "production"),
        "default.shared.environment: set by the bridge call or CI"),
    "unknown option": (
        _set(["api", "native", "sendDefaultPii"], True),
        "api.native.sendDefaultPii: unknown option for python"),
    "typo in shared option": (
        _set(["default", "shared", "max_breadcrumb"], 10),
        "default.shared.max_breadcrumb: unknown option"),
    "option from another platform": (
        _set(["api", "environments", "production", "replays_session_sample_rate"], 0.1),
        "api.environments.production.replays_session_sample_rate: unknown option for python"),
    "rate above 1": (
        _set(["default", "environments", "production", "traces_sample_rate"], 1.5),
        "default.environments.production.traces_sample_rate: must be a number from 0 to 1"),
    "rate below 0": (
        _set(["web-app", "environments", "production", "traces_sample_rate"], -0.1),
        "web-app.environments.production.traces_sample_rate: must be a number from 0 to 1"),
    "rate as a string": (
        _set(["default", "environments", "staging", "sample_rate"], "0.5"),
        "default.environments.staging.sample_rate: must be a number from 0 to 1"),
    "rate as a boolean": (
        _set(["default", "environments", "staging", "traces_sample_rate"], True),
        "default.environments.staging.traces_sample_rate: must be a number from 0 to 1"),
    "environment typo": (
        _set(["web-app", "environments", "prodution"], {"traces_sample_rate": 0.2}),
        "web-app.environments.prodution: not defined in default"),
    "missing root": (
        _delete("web-app", "root"),
        "web-app: missing root"),
    "unknown platform": (
        _set(["web-app", "platform"], "ruby"),
        "web-app: platform must be one of"),
    "environment at the top of a project": (
        _set(["api", "production"], {"traces_sample_rate": 0.2}),
        "api.production: unexpected key"),
    "unknown platform in default.native": (
        _set(["default", "native", "ruby"], {}),
        "default.native.ruby: unknown platform"),
    "missing default": (
        _delete("default"),
        "default: missing, or has no environments block"),
}


@pytest.mark.parametrize("mutate, message", INVALID.values(), ids=INVALID.keys())
def test_invalid_config_is_reported(config, mutate, message):
    mutate(config)
    errors = generate.validate(config)
    assert any(message in error for error in errors), errors


def test_every_problem_is_reported_at_once(config):
    INVALID["missing root"][0](config)
    INVALID["rate above 1"][0](config)
    assert len(generate.validate(config)) == 2


def _integration_errors(config, project):
    errors = []
    generate.check_resolved(generate.resolve(config, project, None), errors)
    return errors


def test_traces_rate_without_browser_tracing_fails(config):
    config["web-app"]["native"]["integrations"]["browserTracing"] = False
    assert "web-app (production): tracesSampleRate is set but the browserTracing integration isn't" \
        in _integration_errors(config, "web-app")


def test_replay_rate_without_replay_fails(config):
    config["web-new"]["native"] = {"integrations": {"replay": False}}
    assert any("replaysSessionSampleRate is set but the replay integration" in e
               for e in _integration_errors(config, "web-new"))


def test_zero_rate_needs_no_integration(config):
    config["web-app"]["environments"] = {
        "production": {"traces_sample_rate": 0},
        "staging": {"traces_sample_rate": 0},
    }
    config["web-app"]["native"]["integrations"]["browserTracing"] = False
    assert _integration_errors(config, "web-app") == []


def test_integration_with_no_options_counts_as_enabled(config):
    # "browserTracing:" with nothing after it loads as None.
    config["default"]["native"]["javascript-browser"]["integrations"]["browserTracing"] = None
    assert _integration_errors(config, "web-app") == []


# --- Command line ------------------------------------------------------------

def test_invalid_config_fails_the_build_and_writes_nothing(config, tmp_path):
    INVALID["rate above 1"][0](config)
    result = run_generate(tmp_path, "--project", "web-app", config=write_config(tmp_path, config))
    assert result.returncode == 1
    assert "traces_sample_rate: must be a number from 0 to 1" in result.stderr
    assert not (tmp_path / "out").exists()


def test_a_broken_project_fails_every_build(config, tmp_path):
    config["worker"]["platform"] = "ruby"
    result = run_generate(tmp_path, "--project", "web-app", config=write_config(tmp_path, config))
    assert result.returncode == 1
    assert "worker: platform must be one of" in result.stderr


def test_unknown_project_fails(tmp_path):
    result = run_generate(tmp_path, "--project", "billing")
    assert result.returncode == 1
    assert "billing: no block in config.yaml" in result.stderr


def test_empty_config_file_fails_cleanly(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("")
    result = run_generate(tmp_path, "--project", "web-app", config=path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
