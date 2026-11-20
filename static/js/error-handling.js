/* Categorised error handling for every page. No inline scripts, no secrets.
 *
 * Internal names below (window.EDUSPHERE_ERRORS, the error banner id and the
 * X-EduSphere-* headers) are kept on purpose: changing them would break live
 * pages and proxies. The catalogue comes from window.EDUSPHERE_ERRORS (served from the shared
 * Python list by /errors/catalogue.js), so browser messages cannot drift from
 * the messages on the server.
 */
(function () {
  "use strict";

  var SLOW_MS = 5000;
  var TIMEOUT_MS = 15000;
  var CATALOGUE = window.EDUSPHERE_ERRORS || {};
  var banner = document.getElementById("edusphere-error-banner");
  var bannerText = banner && banner.querySelector("[data-error-banner-text]");
  var bannerIcon = banner && banner.querySelector("[data-error-banner-icon]");
  var bannerRetry = banner && banner.querySelector("[data-error-banner-retry]");
  var slowTimer = null;
  var timeoutTimer = null;
  var lastRequest = null;
  var retrying = false;

  function spec(code) {
    return CATALOGUE[code] || {
      code: code || "SRV-500",
      category_label: "Server problem",
      icon: "\u25a0",
      headline: "Server problem: our system had a fault, it is not your internet (SRV-500)",
      action: "Try again. If it keeps happening, send us the reference number."
    };
  }

  function showBanner(code, extra) {
    var item = spec(code);
    if (!banner || !bannerText) { return; }
    banner.setAttribute("data-error-code", item.code);
    if (bannerIcon) { bannerIcon.textContent = item.icon || "\u25cf"; }
    bannerText.textContent = (extra || item.headline) + " " + (item.action || "");
    banner.hidden = false;
  }

  function hideBanner() {
    if (banner) { banner.hidden = true; }
  }

  function showRetry(retry) {
    if (!bannerRetry) { return; }
    bannerRetry.hidden = false;
    bannerRetry.onclick = retry || null;
  }

  function clearTimers() {
    if (slowTimer) { clearTimeout(slowTimer); slowTimer = null; }
    if (timeoutTimer) { clearTimeout(timeoutTimer); timeoutTimer = null; }
    if (bannerRetry) { bannerRetry.hidden = true; bannerRetry.onclick = null; }
  }

  function startTimers(onTimeout) {
    clearTimers();
    slowTimer = setTimeout(function () {
      showBanner("NET-SLOW", "This is taking longer than usual");
    }, SLOW_MS);
    timeoutTimer = setTimeout(function () {
      clearTimers();
      showBanner("NET-TIMEOUT");
      showRetry(onTimeout);
    }, TIMEOUT_MS);
  }

  function newKey() {
    if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
    return "k" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function ensureKey(form) {
    var field = form.querySelector('[name="idempotency_key"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "idempotency_key";
      form.appendChild(field);
    }
    if (!field.value) { field.value = newKey(); }
    return field.value;
  }

  function rememberForm(form) {
    /* Never store passwords or other secrets in session storage. */
    var safe = [];
    var fields = form.querySelectorAll("input, textarea, select");
    Array.prototype.forEach.call(fields, function (field) {
      if (!field.name) { return; }
      if (field.type === "password" || field.name.toLowerCase().indexOf("password") !== -1) { return; }
      if (field.type === "file") { return; }
      if (field.type === "checkbox" || field.type === "radio") {
        if (field.checked) { safe.push([field.name, field.value]); }
        return;
      }
      safe.push([field.name, field.value]);
    });
    try { sessionStorage.setItem("edusphere:form:" + form.action + ":" + form.method, JSON.stringify(safe)); } catch (e) { /* storage may be disabled */ }
    return safe;
  }

  function restoreForm(form) {
    var stored;
    try { stored = sessionStorage.getItem("edusphere:form:" + form.action + ":" + form.method); } catch (e) { return; }
    if (!stored) { return; }
    var safe;
    try { safe = JSON.parse(stored); } catch (e) { return; }
    Array.prototype.forEach.call(safe, function (pair) {
      var field = form.querySelector('[name="' + pair[0] + '"]');
      if (field && field.type !== "password" && field.value === "") { field.value = pair[1]; }
    });
  }

  function codeFromResponse(response) {
    var code = response.headers && response.headers.get("X-EduSphere-Error-Code");
    if (code) { return code; }
    var status = response.status;
    if (status === 429) { return "SRV-429"; }
    if (status === 404) { return "NOT-FOUND"; }
    if (status === 403) { return "PERM-DENIED"; }
    if (status === 401) { return "AUTH-REQUIRED"; }
    if (status === 409) { return "DB-CONFLICT"; }
    if (status === 408) { return "NET-TIMEOUT"; }
    if (status === 503) { return "SRV-503"; }
    if (status === 504) { return "SRV-504"; }
    if (status >= 500) { return "SRV-500"; }
    return "INPUT-INVALID";
  }

  function classifyFetchFailure(error, response, timedOut) {
    if (timedOut) { return "NET-TIMEOUT"; }
    if (response) { return codeFromResponse(response); }
    if (navigator.onLine === false) { return "NET-OFFLINE"; }
    return "NET-UNREACHABLE";
  }

  var originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function (input, init) {
      init = init || {};
      var method = (init.method || (input && input.method) || "GET").toUpperCase();
      var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      var key = newKey();
      if (controller) { init.signal = controller.signal; }
      if (method !== "GET" && method !== "HEAD") {
        var headers = new Headers(init.headers || {});
        if (!headers.has("Idempotency-Key")) { headers.set("Idempotency-Key", key); }
        init.headers = headers;
      }
      var call = { type: "fetch", input: input, init: init, key: key };
      lastRequest = call;
      var timedOut = false;
      startTimers(function () {
        if (controller) { controller.abort(); }
        timedOut = true;
      });
      return originalFetch.call(window, input, init).then(function (response) {
        if (!response.ok) {
          showBanner(codeFromResponse(response));
        } else if (response.status === 200 && response.headers &&
                   response.headers.get("Content-Type") === "application/json") {
          /* A 200 with an error body is still an error from the server. */
          response.clone().json().then(function (body) {
            if (body && body.error) { showBanner(body.code || "INPUT-INVALID"); }
          }).catch(function () { /* not an error body */ });
        }
        return response;
      }).catch(function (error) {
        clearTimers();
        showBanner(classifyFetchFailure(error, null, timedOut));
        showRetry(function () { window.fetch(call.input, call.init); });
        throw error;
      }).then(function (response) {
        clearTimers();
        return response;
      });
    };
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM") { return; }
    var method = (form.method || "GET").toUpperCase();
    if (method !== "POST") { return; }
    var key = ensureKey(form);
    var safe = rememberForm(form);
    lastRequest = { type: "form", form: form, key: key, data: safe };
    if (navigator.onLine === false) {
      event.preventDefault();
      showBanner("NET-OFFLINE");
      showRetry(function () { retryForm(form, key); });
      return;
    }
    if (form.dataset.submitting === "1") { event.preventDefault(); return; }
    form.dataset.submitting = "1";
    startTimers(function () {
      showBanner("NET-TIMEOUT");
      showRetry(function () { retryForm(form, key); });
    });
    window.setTimeout(function () { form.dataset.submitting = "0"; }, TIMEOUT_MS + 1000);
  }, true);

  function retryForm(form, key) {
    if (retrying) { return; }
    retrying = true;
    restoreForm(form);
    ensureKey(form).value = key;
    clearTimers();
    hideBanner();
    form.dataset.submitting = "0";
    form.submit();
    retrying = false;
  }

  function retrySameRequest(button) {
    if (!lastRequest || retrying) { return; }
    retrying = true;
    clearTimers();
    hideBanner();
    if (lastRequest.type === "fetch") {
      var call = lastRequest;
      retrying = false;
      window.fetch(call.input, call.init);
      return;
    }
    var form = lastRequest.form;
    if (form && document.body.contains(form)) {
      retryForm(form, lastRequest.key);
    } else if (button && button.dataset.retryUrl) {
      window.location.assign(button.dataset.retryUrl);
    }
    retrying = false;
  }

  document.addEventListener("click", function (event) {
    var retry = event.target.closest && event.target.closest("[data-retry]");
    if (retry) {
      event.preventDefault();
      retrySameRequest(retry);
      return;
    }
    var copy = event.target.closest && event.target.closest("[data-copy-reference]");
    if (copy) {
      var value = copy.dataset.copyValue || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(function () {
          copy.textContent = "Copied";
        }).catch(function () { copy.textContent = "Copy: " + value; });
      } else {
        copy.textContent = "Copy: " + value;
      }
      return;
    }
    var back = event.target.closest && event.target.closest("[data-go-back]");
    if (back && document.referrer && !back.getAttribute("href")) {
      back.setAttribute("href", document.referrer);
    }
  });

  window.addEventListener("offline", function () {
    showBanner("NET-OFFLINE", "You are offline. Your changes will not be saved until you are back online");
  });
  window.addEventListener("online", function () {
    clearTimers();
    hideBanner();
  });

  /* Exposed for integration tests and for other first-party scripts. */
  window.EdusphereErrors = {
    showBanner: showBanner,
    hideBanner: hideBanner,
    classifyFetchFailure: classifyFetchFailure,
    codeFromResponse: codeFromResponse,
    ensureKey: ensureKey,
    spec: spec
  };
})();
