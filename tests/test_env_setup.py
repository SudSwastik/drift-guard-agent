"""Secret generation preserves configured values and does not disclose credentials."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "scripts" / "configure-env.py"
spec = importlib.util.spec_from_file_location("configure_env", SCRIPT)
assert spec is not None and spec.loader is not None
configure_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure_env)


def test_secret_generation_is_private_repeatable_and_preserves_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "DRIFT_GUARD_API_KEY=keep-existing-private-key\nDRIFT_GUARD_ENVIRONMENT=production\nDRIFT_GUARD_POSTGRES_PASSWORD=\n"
    )
    generated = configure_env.configure(path)
    assert "DRIFT_GUARD_API_KEY" not in generated
    content = path.read_text()
    assert "DRIFT_GUARD_API_KEY=keep-existing-private-key" in content
    assert "DRIFT_GUARD_ENVIRONMENT=production" in content
    assert path.stat().st_mode & 0o777 == 0o600
    assert configure_env.configure(path) == []
    assert path.read_text() == content
    assert len(list(tmp_path.iterdir())) == 1
    values = dict(line.split("=", 1) for line in content.splitlines())
    assert values["DRIFT_GUARD_POSTGRES_PASSWORD"] != values["DRIFT_GUARD_POSTGRES_ADMIN_PASSWORD"]


def test_duplicate_secret_configuration_does_not_overwrite_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    content = "DRIFT_GUARD_API_KEY=first\nDRIFT_GUARD_API_KEY=second\n"
    path.write_text(content)
    with pytest.raises(ValueError, match="Remove duplicate"):
        configure_env.configure(path)
    assert path.read_text() == content
