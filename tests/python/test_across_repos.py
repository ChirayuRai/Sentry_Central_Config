"""The pipeline step from the README's "Each app repo" section, run the way a customer's app
repo runs it: in an app repo that's separate from the central config repo."""
import os
import re
import shutil
import subprocess
import sys
import textwrap

import yaml

from conftest import CONFIG_DIR, REPO

# Git runs without the user's own settings (commit signing, hooks, default branch name).
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def readme_step(central, project):
    """The shell lines in the README's "Each app repo" section, with the placeholders filled in."""
    section = (REPO / "README.md").read_text().split("\n### Each app repo\n", 1)[1]
    step = textwrap.dedent(re.search(r"```\w*\n(.*?)```", section, re.S).group(1))
    step = (step.replace("<central config repo>", central.as_uri())
                .replace("<name>", project).replace("<version>", "1.0.0"))
    assert not re.search(r"<[^>]+>", step), f"unfilled placeholder in the README step:\n{step}"
    return step


def test_an_app_repo_builds_with_the_central_config_and_picks_up_its_changes(tmp_path):
    # The central repo: this repo's sentry-config folder, with a config for one app.
    central = tmp_path / "central"
    shutil.copytree(CONFIG_DIR, central / "sentry-config", ignore=shutil.ignore_patterns("__pycache__"))
    git(central, "init", "-b", "main")

    def set_production_traces_rate(rate):
        config = {"default": {"environments": {"production": {"traces_sample_rate": rate}}},
                  "api": {"root": "app", "platform": "python"}}
        (central / "sentry-config" / "sentry_main_config.yaml").write_text(yaml.safe_dump(config))
        commit(central, f"Production traces at {rate}")

    set_production_traces_rate(0.1)

    # A separate app repo holding the example Python app.
    app = tmp_path / "api"
    (app / "app").mkdir(parents=True)
    shutil.copy(REPO / "examples" / "api" / "app" / "instrument.py", app / "app")
    git(app, "init", "-b", "main")
    commit(app, "App")

    # Point the step's python3 at the interpreter running these tests. It already has
    # PyYAML, so the step's pip install is skipped and the test needs no network.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("python", "python3"):
        (bin_dir / name).write_text(f'#!/bin/sh\n[ "$1 $2" = "-m pip" ] && exit 0\nexec "{sys.executable}" "$@"\n')
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in GIT_ENV.items() if k != "SENTRY_DSN"}  # no DSN: nothing is sent
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    def build_and_start():
        """Run the app repo's pipeline step, then start the app and return the SDK's traces rate."""
        built = subprocess.run(["sh", "-ec", readme_step(central, "api")], cwd=app, env=env,
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        script = "import instrument, sentry_sdk; print(sentry_sdk.get_client().options['traces_sample_rate'])"
        started = subprocess.run([sys.executable, "-c", script], cwd=app / "app",
                                 env={**env, "APP_ENV": "production"}, capture_output=True, text=True)
        assert started.returncode == 0, started.stderr
        return float(started.stdout)

    assert build_and_start() == 0.1
    set_production_traces_rate(0.3)   # one change in the central repo
    assert build_and_start() == 0.3   # reaches the app on its next build, with no change to the app repo
