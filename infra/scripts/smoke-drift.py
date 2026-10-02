"""Seed/replay observations and verify PostgreSQL data survives application rollback."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def call(
    base_url: str, path: str, body: dict[str, object] | None = None, key: str | None = None
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("DRIFT_GUARD_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    if key:
        headers["Idempotency-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(
        Request(base_url.rstrip("/") + path, data=data, headers=headers), timeout=15
    ) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("phase", choices=["seed", "verify", "outage"])
    parser.add_argument("--snapshot-file", type=Path, required=True)
    args = parser.parse_args()
    query = "/v1/drift/payments/v1?operation=POST%20%2Fpayments&direction=request"
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "validation-cases.json"
    cases = json.loads(fixture.read_text())
    body = {
        "api": "payments",
        "version": "v1",
        "operation": "POST /payments",
        "direction": "request",
        "payload": cases[1]["payload"],
    }
    if args.phase == "seed":
        baseline = call(args.base_url, query)["sampleCount"]
        key = "smoke-" + uuid4().hex
        first = call(args.base_url, "/v1/validate", body, key)
        assert first["observation"]["status"] == "stored"
        for index in range(1, 10):
            body["payload"] = cases[1 if index < 3 else 0]["payload"]
            assert (
                call(args.base_url, "/v1/validate", body, f"{key}-{index}")["observation"]["status"]
                == "stored"
            )
        report = call(args.base_url, query)
        assert report["sampleCount"] == baseline + 10
        args.snapshot_file.write_text(
            json.dumps({"report": report, "key": key, "id": first["observation"]["id"]})
        )
        print("PASS PostgreSQL observations stored with exact counts")
    elif args.phase == "verify":
        snapshot = json.loads(args.snapshot_file.read_text())
        duplicate = call(args.base_url, "/v1/validate", body, snapshot["key"])
        assert duplicate["observation"] == {"status": "duplicate", "id": snapshot["id"]}
        report = call(args.base_url, query)
        for field in (
            "sampleCount",
            "invalidCount",
            "findings",
            "contractHash",
            "policyVersion",
            "validatorVersion",
        ):
            assert report[field] == snapshot["report"][field], field
        print("PASS PostgreSQL counts and idempotency survived application rollback")
    else:
        snapshot = json.loads(args.snapshot_file.read_text())
        try:
            call(args.base_url, "/v1/validate", body, snapshot["key"])
        except HTTPError as response:
            assert response.code == 503
            assert json.load(response)["error"]["code"] == "observation_storage_unavailable"
        else:
            raise AssertionError("Required storage outage should return 503")
        status = call(args.base_url, "/v1/observations/status")
        assert not status["storageReady"] and status["counters"]["writeFailures"] >= 1
        for path in (query, "/health/ready"):
            try:
                call(args.base_url, path)
            except HTTPError as response:
                assert response.code == 503
            else:
                raise AssertionError("Storage-dependent endpoint should return 503")
        print("PASS PostgreSQL outage is visible in responses, readiness, and counters")


if __name__ == "__main__":
    main()
