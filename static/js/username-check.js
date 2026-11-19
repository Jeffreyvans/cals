/* Progressive enhancement only: the server always validates the username. */
(function () {
  "use strict";
  var inputs = document.querySelectorAll("[data-username-check-url]");
  if (!inputs.length || !window.fetch) return;

  Array.prototype.forEach.call(inputs, function (input) {
    var status = input.parentNode.querySelector("[data-username-status]");
    if (!status) {
      status = document.createElement("span");
      status.setAttribute("data-username-status", "");
      status.setAttribute("aria-live", "polite");
      status.className = "username-status";
      input.insertAdjacentElement("afterend", status);
    }
    var timer = null;
    var serial = 0;

    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      serial += 1;
      var mine = serial;
      var value = input.value.trim();
      if (!value) {
        status.textContent = "";
        status.className = "username-status";
        return;
      }
      status.textContent = "Checking…";
      status.className = "username-status";
      timer = window.setTimeout(function () {
        var url = input.getAttribute("data-username-check-url");
        var exclude = input.getAttribute("data-username-exclude") || "";
        var query = "?username=" + encodeURIComponent(value);
        if (exclude) query += "&exclude=" + encodeURIComponent(exclude);
        fetch(url + query, { headers: { "X-Requested-With": "XMLHttpRequest" } })
          .then(function (response) { return response.ok ? response.json() : null; })
          .then(function (data) {
            if (!data || mine !== serial) return;
            status.textContent = data.message || "";
            status.className = "username-status " + (data.available ? "ok" : "error");
          })
          .catch(function () {
            if (mine === serial) status.textContent = "";
          });
      }, 300);
    });
  });
})();
