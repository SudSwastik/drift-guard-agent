"""Exercise release failure and rollback logic without touching the Docker host."""

import json
import os
import subprocess
from pathlib import Path

import pytest

RELEASE_SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "scripts" / "release.sh"
FAKE_DOCKER = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["FAKE_DOCKER_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if args[0] == "compose":
    assert args[1] == "--project-directory"
    assert args[3] == "--file"
    assert Path(args[4]).name == "compose.yaml" and Path(args[4]).exists()
    args = ["compose", *args[5:]]
if args[:2] == ["image", "inspect"]:
    target = args[-1]
    image = state["images"].get(target)
    if not image:
        sys.exit(1)
    print(image)
elif args[0] == "inspect":
    print(state["current"])
elif args[:2] == ["compose", "ps"]:
    print("test-container" if state["current"] else "")
elif args[:2] == ["compose", "up"]:
    if args[-1] == "postgres":
        sys.exit(0)
    state["current"] = os.environ["DRIFT_GUARD_IMAGE"]
    path.write_text(json.dumps(state))
    sys.exit(1 if state["current"] == "sha256:bad" else 0)
elif args[:2] != ["compose", "logs"]:
    raise SystemExit("Unexpected docker command: " + repr(args))
"""


@pytest.fixture
def release_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    executable = tmp_path / "docker"
    executable.write_text(FAKE_DOCKER)
    executable.chmod(0o755)
    docker_state = tmp_path / "docker-state.json"
    docker_state.write_text(
        json.dumps(
            {
                "images": {
                    "candidate": "sha256:new",
                    "broken": "sha256:bad",
                    "sha256:old": "sha256:old",
                },
                "current": "sha256:old",
            }
        )
    )
    deployment_state = tmp_path / "deployment"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(docker_state),
        "DRIFT_GUARD_STATE_DIR": str(deployment_state),
    }
    return env, docker_state, deployment_state


def run_release(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RELEASE_SCRIPT), *args], env=env, capture_output=True, text=True, timeout=10
    )


def test_successful_deploy_can_restore_exact_previous_image(
    release_environment: tuple[dict[str, str], Path, Path],
) -> None:
    env, docker_state, state_dir = release_environment
    deployed = run_release(env, "deploy", "candidate")
    assert deployed.returncode == 0, deployed.stderr
    assert (state_dir / "previous-image").read_text().strip() == "sha256:old"
    restored = run_release(env, "rollback")
    assert restored.returncode == 0, restored.stderr
    assert json.loads(docker_state.read_text())["current"] == "sha256:old"
    assert (state_dir / "current-image").read_text().strip() == "sha256:old"


def test_failed_deploy_recovers_previous_container(
    release_environment: tuple[dict[str, str], Path, Path],
) -> None:
    env, docker_state, state_dir = release_environment
    result = run_release(env, "deploy", "broken")
    assert result.returncode == 1
    assert "Restoring previous image" in result.stderr
    assert json.loads(docker_state.read_text())["current"] == "sha256:old"
    assert not (state_dir / "current-image").exists()
    assert not (state_dir / "lock").exists()


def test_unknown_image_does_not_replace_running_container(
    release_environment: tuple[dict[str, str], Path, Path],
) -> None:
    env, docker_state, _ = release_environment
    assert run_release(env, "deploy", "missing-image").returncode != 0
    assert json.loads(docker_state.read_text())["current"] == "sha256:old"


def test_rollback_requires_recorded_previous_release(
    release_environment: tuple[dict[str, str], Path, Path],
) -> None:
    env, docker_state, _ = release_environment
    result = run_release(env, "rollback")
    assert result.returncode == 1
    assert "No previous release recorded" in result.stderr
    assert json.loads(docker_state.read_text())["current"] == "sha256:old"
