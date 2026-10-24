"""Probe from inside the CI container: real API boot with an unavailable test DB."""

import json
import time
import urllib.error
import urllib.request


def main(base_url: str = "http://127.0.0.1:8000") -> None:
    for attempt in range(40):
        try:
            with urllib.request.urlopen(base_url + "/health/live", timeout=2) as response:
                assert response.status == 200 and json.load(response) == {"status": "ok"}
            break
        except (OSError, urllib.error.URLError):
            if attempt == 39:
                raise RuntimeError("Container API did not become live") from None
            time.sleep(0.25)
    with urllib.request.urlopen(base_url + "/openapi.json", timeout=2) as response:
        document = json.load(response)
        count = sum(
            method in {"get", "post"} for path in document["paths"].values() for method in path
        )
        assert count == 18
    try:
        urllib.request.urlopen(base_url + "/health/ready", timeout=10).close()
    except urllib.error.HTTPError as error:
        assert error.code == 503
        assert json.load(error)["error"]["retryable"] is True
    else:
        raise AssertionError("Unavailable test database must not report readiness")
    print("Container API boot, liveness, OpenAPI and dependency failure checks passed")


if __name__ == "__main__":
    main()
