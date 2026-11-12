import copy
import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest
import sentry_sdk
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "sentry-config"
FIXTURES = REPO / "tests" / "fixtures"
FIXTURE_CONFIG = FIXTURES / "config.yaml"
EXPECTED = FIXTURES / "expected"
RELEASE = "1.4.2"
DSN = "https://public@o0.ingest.sentry.io/0"
# Project name -> platform, for every project in the fixture config.
PLATFORMS = {name: block["platform"] for name, block in yaml.safe_load(FIXTURE_CONFIG.read_text()).items()
             if name != "default"}
PROJECTS = list(PLATFORMS)

sys.path.insert(0, str(CONFIG_DIR))
import generate  # noqa: E402


@pytest.fixture
def config():
    """A fresh copy of the fixture config, safe to modify in a test."""
    return copy.deepcopy(yaml.safe_load(FIXTURE_CONFIG.read_text()))


def golden(project):
    return json.loads((EXPECTED / f"{project}.json").read_text())


def import_file(path):
    """Import a module from a file path, under a name unique to that file."""
    spec = importlib.util.spec_from_file_location(f"bridge_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_bridge(tmp_path):
    """Put a snapshot next to a copy of the Python bridge, as generate.py does,
    and import that copy. Pass a project name to use its golden snapshot, or a dict."""

    def load(snapshot):
        if isinstance(snapshot, str):
            snapshot = golden(snapshot)
        folder = tmp_path / uuid.uuid4().hex
        folder.mkdir()
        (folder / generate.SNAPSHOT_FILE).write_text(json.dumps(snapshot))
        shutil.copy(CONFIG_DIR / "bridges" / "sentry_bridge.py", folder)
        return import_file(folder / "sentry_bridge.py")

    return load


@pytest.fixture(autouse=True)
def reset_sentry():
    yield
    client = sentry_sdk.get_client()
    if client.is_active():
        client.close(timeout=0)
    sentry_sdk.get_global_scope().set_client(None)
