/* EduSphere app shell.
 *
 * The worker keeps the small, shared shell (CSS, JS and an offline page) on the
 * device so a repeat visit on a metered connection starts instantly. It never
 * caches HTML pages that belong to a signed-in person and never touches a POST.
 */
const CACHE = "edusphere-shell-v1";
const SHELL = [
  "/static/css/app.css",
  "/static/css/public.css",
  "/static/js/app.js",
  "/static/js/landing.js",
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
  return url.pathname.startsWith("/static/") || url.pathname === "/sw.js";
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
    // Network-first for pages; fall back to the tiny offline page.
    event.respondWith(
      fetch(request).catch(() => caches.match("/offline/"))
    );
  }
});
