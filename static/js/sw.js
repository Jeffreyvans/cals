/* Vanpere app shell.
 *
 * The worker keeps the small, shared shell (CSS, JS and an offline page) on the
 * device so a repeat visit on a metered connection starts instantly. It never
 * caches HTML pages that belong to a signed-in person and never touches a POST.
 */
// Changing the cache name on a rebrand forces browsers to fetch fresh assets.
const CACHE = "vanpere-shell-v2";
const SHELL = [
  "/static/css/app.css",
  "/static/css/public.css",
  "/static/js/app.js",
  "/static/js/landing.js",
  "/static/js/error-handling.js",
  "/errors/catalogue.js",
  "/offline/"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

function isStatic(url) {
  return url.pathname.startsWith("/static/") || url.pathname === "/sw.js" ||
    url.pathname === "/errors/catalogue.js";
}

function mayCachePage(response) {
  /* The server sets this header only for anonymous public pages. */
  return response.ok &&
    // Internal header name: kept so deployed proxies keep working.
    response.headers.get("X-EduSphere-Offline-Public") === "1" &&
    !response.headers.has("Set-Cookie") &&
    !/no-store|private/.test(response.headers.get("Cache-Control") || "");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isStatic(url)) {
    // Cache-first: the collected file names are hashed, so they never change.
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }))
    );
    return;
  }

  if (request.mode === "navigate") {
    // Network-first for pages. Only explicitly public anonymous pages may be
    // stored; signed-in HTML is never cached or replayed.
    event.respondWith(
      fetch(request).then((response) => {
        if (mayCachePage(response)) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match(request)
        .then((hit) => hit || caches.match("/offline/")))
    );
  }
});
