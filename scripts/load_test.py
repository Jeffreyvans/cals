#!/usr/bin/env python3
"""Small, dependency-light HTTP load test for Vanpere.

This is a smoke/soak tool, not a substitute for a proper staged load test. It
uses only ``requests`` (already installed for tests) and the standard library.

Examples
--------
    python scripts/load_test.py --base http://127.0.0.1:8000 \
        --paths /health/ /status/ /about/ --users 10 --requests 20

    python scripts/load_test.py --base http://127.0.0.1:8000 \
        --paths /health/ --users 25 --requests 40 --json results.json

The script refuses to hit a host that is not localhost unless --confirm-remote
is given, so it cannot be pointed at production by accident.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--paths", nargs="+", default=["/health/", "/status/", "/about/"])
    parser.add_argument("--users", type=int, default=10, help="Concurrent workers")
    parser.add_argument("--requests", type=int, default=20, help="Requests per worker")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", dest="json_path", default=None, help="Write raw results here")
    parser.add_argument("--confirm-remote", action="store_true")
    args = parser.parse_args(argv)
    if args.users < 1 or args.requests < 1:
        parser.error("--users and --requests must be at least 1")
    parsed = urlparse(args.base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        parser.error("--base must be an http(s) URL")
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"} and not args.confirm_remote:
        parser.error("refusing a non-local target without --confirm-remote")
    return args


def run_worker(base, paths, count, timeout):
    results = []
    session = requests.Session()
    for index in range(count):
        path = paths[index % len(paths)]
        url = f"{base.rstrip('/')}{path}"
        started = time.perf_counter()
        status = None
        error = ""
        try:
            response = session.get(url, timeout=timeout)
            status = response.status_code
            if status >= 400:
                error = f"HTTP {status}"
        except requests.RequestException as exc:
            error = type(exc).__name__
        elapsed = (time.perf_counter() - started) * 1000
        results.append({"path": path, "status": status, "ms": elapsed, "error": error})
    session.close()
    return results


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarise(rows, seconds):
    latencies = [row["ms"] for row in rows]
    failures = [row for row in rows if row["error"]]
    return {
        "requests": len(rows),
        "seconds": round(seconds, 3),
        "requests_per_second": round(len(rows) / seconds, 1) if seconds else 0,
        "errors": len(failures),
        "error_rate_pct": round(100 * len(failures) / len(rows), 2) if rows else 0,
        "min_ms": round(min(latencies), 1) if latencies else 0,
        "mean_ms": round(statistics.mean(latencies), 1) if latencies else 0,
        "p50_ms": round(percentile(latencies, 0.50), 1),
        "p95_ms": round(percentile(latencies, 0.95), 1),
        "p99_ms": round(percentile(latencies, 0.99), 1),
        "max_ms": round(max(latencies), 1) if latencies else 0,
        "by_path": {
            path: {
                "requests": len(path_rows),
                "p95_ms": round(percentile([r["ms"] for r in path_rows], 0.95), 1),
                "errors": len([r for r in path_rows if r["error"]]),
            }
            for path, path_rows in {
                path: [r for r in rows if r["path"] == path]
                for path in sorted({r["path"] for r in rows})
            }.items()
        },
    }


def main(argv=None):
    args = parse_args(argv)
    started = time.perf_counter()
    rows = []
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [
            pool.submit(run_worker, args.base, args.paths, args.requests, args.timeout)
            for _ in range(args.users)
        ]
        for future in as_completed(futures):
            rows.extend(future.result())
    seconds = time.perf_counter() - started
    summary = summarise(rows, seconds)
    print(json.dumps(summary, indent=2))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump({"summary": summary, "rows": rows}, handle, indent=2)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
