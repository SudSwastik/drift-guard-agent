"""Check golden validation cases against a running service using the standard library."""

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="Service base URL, for example http://localhost:8080")
    parser.add_argument("--mode", choices=["report_only", "enforce"], default="report_only")
    args = parser.parse_args()
    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "validation-cases.json"
    cases = json.loads(fixtures.read_text())
    key = os.environ.get("DRIFT_GUARD_API_KEY")
    for case in cases:
        body = json.dumps(
            {
                "api": "payments",
                "version": "v1",
                "operation": "POST /payments",
                "direction": "request",
                "payload": case["payload"],
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-Request-ID": "validation-smoke"}
        if key:
            headers["X-API-Key"] = key
        request = Request(args.base_url.rstrip("/") + "/v1/validate", data=body, headers=headers)
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
            assert response.status == 200, case["name"]
            assert response.headers["X-Request-ID"] == result["requestId"], case["name"]
        assert result["mode"] == args.mode, case["name"]
        assert result["severity"] == case["severity"], case["name"]
        assert [[item["code"], item["path"]] for item in result["findings"]] == case["findings"], (
            case["name"]
        )
        expected = "ALLOW" if not case["findings"] else "WARN"
        if args.mode == "enforce" and case["severity"] in ("MEDIUM", "HIGH"):
            expected = "BLOCK"
        assert result["decision"] == expected, case["name"]
        assert len(result["contractHash"]) == 64, case["name"]
        print(f"PASS {case['name']}: {result['decision']}")


if __name__ == "__main__":
    main()
