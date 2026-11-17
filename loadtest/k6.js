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

  http.post(
    `${HOST}/login/`,
    {
      username: __ENV.LOAD_USERNAME,
      password: __ENV.LOAD_PASSWORD,
      csrfmiddlewaretoken: token,
    },
    { headers: { Referer: `${HOST}/login/` } }
  );

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
