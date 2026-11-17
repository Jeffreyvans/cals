# Load testing EduSphere

The goal is not a vanity request-per-second number. It is to answer three
questions before a term starts:

1. **Does a page stay under ~25 queries and ~300 KB** when the table has
   10,000+ rows?
2. **How many concurrent staff** can the current web service and database
   plan serve before p95 latency crosses about 1 second?
3. **Does anything fall over** — connection pool exhaustion, worker timeouts,
   N+1 growth, memory growth — before real users arrive?

Run the load test against a **staging copy**, never the live school database.
Use a recent production dump so the indexes are tested against real row counts.
Restore it with the same PostgreSQL version and run `ANALYZE` afterwards.

## What to test

| Flow | Request(s) | Why |
| --- | --- | --- |
| Sign in | `GET /login/`, `POST /login/` | Session, password hash, permission lookup |
| Dashboard | `GET /dashboard/` | Aggregates, cache, role-specific widgets |
| Student search | `GET /students/?q=...` | The busiest list and its pagination |
| Student record | `GET /students/<id>/` | Related balances, enrolments, guardians |
| Payment page | `GET /finance/pay/<id>/` | Finance form and fee lookup |
| Admissions list | `GET /admissions/applications/` | Filtered, paginated office queue |
| Public page | `GET /` and `GET /i/<slug>/` | Anonymous traffic, cache and static files |

Do **not** POST a payment, save a mark or send an email in the load test. Read
paths are enough to find the N+1s and the pool limits; writes need a separate,
smaller test with a disposable database.

## Locust

Install it in a separate virtual environment so it never becomes a production
dependency:

```bash
python -m venv .venv-load
.venv-load/Scripts/activate        # Windows
pip install locust
```

Save this as `loadtest/locustfile.py` (the repository keeps the example; the
virtual environment and reports are ignored):

```python
"""Read-only EduSphere load test: login, dashboard, search, payment page."""

import os
import re

from locust import HttpUser, between, task

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
USERNAME = os.environ.get("LOAD_USERNAME", "")
PASSWORD = os.environ.get("LOAD_PASSWORD", "")
STUDENT_ID = os.environ.get("LOAD_STUDENT_ID", "")
SEARCH_TERM = os.environ.get("LOAD_SEARCH_TERM", "a")


class StaffUser(HttpUser):
    wait_time = between(1, 4)
    host = os.environ.get("LOAD_HOST", "http://127.0.0.1:8000")

    def on_start(self):
        page = self.client.get("/login/", name="login page")
        match = CSRF_RE.search(page.text)
        token = match.group(1) if match else ""
        self.client.post(
            "/login/",
            data={
                "username": USERNAME,
                "password": PASSWORD,
                "csrfmiddlewaretoken": token,
            },
            headers={"Referer": f"{self.host}/login/"},
            name="login submit",
        )

    @task(4)
    def dashboard(self):
        self.client.get("/dashboard/", name="dashboard")

    @task(3)
    def student_search(self):
        self.client.get(f"/students/?q={SEARCH_TERM}", name="student search")

    @task(2)
    def admissions(self):
        self.client.get("/admissions/applications/", name="admissions list")

    @task(2)
    def student_record(self):
        if STUDENT_ID:
            self.client.get(f"/students/{STUDENT_ID}/", name="student record")

    @task(1)
    def payment_page(self):
        if STUDENT_ID:
            self.client.get(f"/finance/pay/{STUDENT_ID}/", name="payment page")

    @task(1)
    def public_home(self):
        self.client.get("/", name="public home")
```

Run it:

```bash
export LOAD_USERNAME="loadtest-registrar"
export LOAD_PASSWORD="a-throwaway-password"
export LOAD_STUDENT_ID="123"
export LOAD_HOST="https://staging.example.com"

locust -f loadtest/locustfile.py --headless \
  -u 100 -r 10 -t 5m --html loadtest/report.html
```

`-u` is concurrent users, `-r` is users added per second, `-t` is the run
duration. Start with 25 users, then 50, 100 and 200. Stop when p95 latency
crosses 1 second or the error rate crosses 1%.

While it runs, watch:

* **Render metrics** — CPU, memory and response time per instance.
* **PostgreSQL** — active connections, slow queries (`SLOW_QUERY_SECONDS` logs
  lines slower than 250 ms), and CPU. A connection count at the plan's limit
  is the usual first wall; fix it with the session pooler, not more workers.
* **Application logs** — `edusphere.slow` lines and any 5xx.
* **`/health/`** — it stays cheap and must keep returning 200.

## k6 alternative

```javascript
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "1m", target: 25 },
    { duration: "2m", target: 100 },
    { duration: "1m", target: 100 },
    { duration: "30s", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<1000"],
    http_req_failed: ["rate<0.01"],
  },
};

const HOST = __ENV.LOAD_HOST || "http://127.0.0.1:8000";
const STUDENT_ID = __ENV.LOAD_STUDENT_ID || "";

export default function () {
  const loginPage = http.get(`${HOST}/login/`);
  const token = loginPage.body.match(
    /name="csrfmiddlewaretoken" value="([^"]+)"/
  )[1];

  http.post(`${HOST}/login/`, {
    username: __ENV.LOAD_USERNAME,
    password: __ENV.LOAD_PASSWORD,
    csrfmiddlewaretoken: token,
  }, { headers: { Referer: `${HOST}/login/` } });

  const pages = [
    `${HOST}/dashboard/`,
    `${HOST}/students/?q=a`,
    `${HOST}/admissions/applications/`,
    `${HOST}/`,
  ];
  if (STUDENT_ID) {
    pages.push(`${HOST}/students/${STUDENT_ID}/`);
    pages.push(`${HOST}/finance/pay/${STUDENT_ID}/`);
  }

  for (const url of pages) {
    const response = http.get(url);
    check(response, { "status is 200": (r) => r.status === 200 });
  }
  sleep(2);
}
```

```bash
k6 run -e LOAD_HOST=https://staging.example.com \
       -e LOAD_USERNAME=loadtest-registrar \
       -e LOAD_PASSWORD=... \
       -e LOAD_STUDENT_ID=123 loadtest/k6.js
```

## Sizing recommendations

These are starting points, not guarantees. Measure on your own data and plan.

| Users | Typical concurrent staff | Web service | Workers × threads | Redis | Celery worker | PostgreSQL |
| --- | --- | --- | --- | --- | --- | --- |
| up to 1,000 | 10–25 | 512 MB | 2 × 2 | optional | optional (inline jobs are fine) | smallest plan, session pooler |
| 1,000–5,000 | 25–75 | 1–2 GB | 3 × 3 | yes | 1 × 2 | 2–4 GB, session pooler, `CONN_MAX_AGE=600` |
| 5,000–10,000+ | 75–200 | 2 GB+, two instances behind a load balancer | 4 × 4 each | yes, with eviction policy | 2 × 4 | 4 GB+, read replica for reports, PgBouncer/session pooler |

Keep `workers × threads` well below the database connection limit. With
`CONN_MAX_AGE=600`, each worker process holds one persistent connection per
thread; 4 workers × 4 threads = 16 connections per instance. Add the Celery
workers and every other service before choosing the plan.

## Pass criteria

* Dashboard, student search, admissions and the payment page: **p95 under
  1 second** at the target concurrency.
* **No 5xx** and error rate under 1%.
* Query count does **not grow** when the page has more rows (check the
  `assertNumQueries` tests and `measure_performance`).
* Memory stays flat across the run; `GUNICORN_MAX_REQUESTS=1000` recycles a
  worker before a leak becomes an outage.
* Public pages are under ~300 KB first load and return `304` on a repeat visit.
* `/health/` responds in well under 100 ms throughout.
