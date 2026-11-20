# Load test results

## Tool and method

Script: `scripts/load_test.py` (standard library + `requests`). It fires
`--users` concurrent workers, each making `--requests` sequential GETs across
the supplied paths, and reports latency percentiles, throughput and error rate.
The script refuses non-local targets unless `--confirm-remote` is supplied, so
it cannot be pointed at production by accident.

Run from the repository root against one local Django development server:

```bash
python manage.py migrate --noinput
python manage.py runserver 127.0.0.1:8000 --noreload
python scripts/load_test.py --base http://127.0.0.1:8000 \
  --paths /health/ /status/ /about/ --users 10 --requests 20 \
  --json pytest-load-results.json
python scripts/load_test.py --base http://127.0.0.1:8000 \
  --paths /health/ /status/ /about/ --users 25 --requests 40 \
  --json pytest-load-results-heavy.json
```

Final clean run date: 2026-11-20. Raw output files: `pytest-load-results.json`
and `pytest-load-results-heavy.json` in the repository root. An earlier
`pytest-load-results-optimized.json` file was discarded because two local
servers were competing for port 8000 during that measurement; its numbers are
not used here.

## Run 1 — smoke (10 users × 20 requests = 200 requests)

| Metric | Value |
| --- | --- |
| Duration | 6.091 s |
| Throughput | 32.8 requests/s |
| Errors | 0 (0.0%) |
| Min / mean | 30.9 ms / 278.1 ms |
| p50 / p95 / p99 | 184.6 ms / 355.1 ms / 2497.6 ms |
| Max | 2536.8 ms |

Per-path p95: `/health/` 276.6 ms, `/status/` 2474.1 ms, `/about/` 271.4 ms.
The `/status/` tail in this short run came from a small number of queued cold
requests against the single-threaded development server; the heavier run below
shows `/status/` settling at 787.5 ms p95.

## Run 2 — heavier smoke (25 users × 40 requests = 1000 requests)

| Metric | Value |
| --- | --- |
| Duration | 22.697 s |
| Throughput | 44.1 requests/s |
| Errors | 0 (0.0%) |
| Min / mean | 47.2 ms / 540.9 ms |
| p50 / p95 / p99 | 454.4 ms / 1204.8 ms / 1528.8 ms |
| Max | 1656.9 ms |

Per-path p95: `/health/` 646.0 ms, `/status/` 787.5 ms, `/about/` 1444.7 ms.

## Interpretation

- No HTTP or connection errors occurred in either run.
- The `core.status` aggregate email query did not dominate the heavier run;
  `/status/` stayed below `/about/` at p95. The short-run outlier is consistent
  with development-server queueing, not a query-plan problem (the test database
  holds only two `OutboundMessage` rows and no slow-query log entries were
  emitted).
- Throughput plateaued around 33–44 RPS and latency rose with concurrency. That
  is expected for the single-threaded development server with SQLite; it is
  **not** a production capacity figure.
- These are anonymous read-only pages. Authenticated tenant pages are excluded
  deliberately so SQLite write contention does not distort a first baseline.

## What is still required before go-live

- Repeat the test against a **production-shaped** stack: gunicorn with multiple
  workers, PostgreSQL and the same instance size as production.
- Exercise authenticated, tenant-scoped pages (dashboard, student list, invoice
  list) with realistic data volumes.
- Define acceptable SLOs (for example p95 < 500 ms for authenticated pages) and
  alert on them; no external uptime monitor is configured in this repository.

## Severity

- **Medium** — a smoke script and baseline numbers exist, but no production-shaped
  load test, SLO or external uptime monitor has been run or configured.
