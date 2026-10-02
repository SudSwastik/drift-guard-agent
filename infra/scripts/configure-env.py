"""Fill missing deployment secrets in the ignored .env without printing their values."""

import argparse
import os
import secrets
from pathlib import Path

SECRET_NAMES = (
    "DRIFT_GUARD_API_KEY",
    "DRIFT_GUARD_POSTGRES_PASSWORD",
    "DRIFT_GUARD_POSTGRES_ADMIN_PASSWORD",
    "DRIFT_GUARD_OBSERVATION_HMAC_KEY",
)


def configure(path: Path) -> list[str]:
    lines = path.read_text().splitlines() if path.exists() else []
    generated = []
    for name in SECRET_NAMES:
        positions = [index for index, line in enumerate(lines) if line.startswith(name + "=")]
        if len(positions) > 1:
            raise ValueError(f"Remove duplicate {name} entries before generating secrets")
        if positions and lines[positions[0]].split("=", 1)[1].strip() not in ("", '""', "''"):
            continue
        value = f"{name}={secrets.token_hex(24)}"
        if positions:
            lines[positions[0]] = value
        else:
            lines.append(value)
        generated.append(name)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            descriptor = -1
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        names = configure(args.path)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
    print(f"Configured {len(names)} missing secrets in {args.path}; existing values preserved.")


if __name__ == "__main__":
    main()
