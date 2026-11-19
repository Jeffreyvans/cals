/* Copy and print helpers for one-time credential screens. No inline scripts. */
(function () {
  "use strict";

  function label(button) {
    var before = button.textContent;
    button.textContent = "Copied";
    window.setTimeout(function () { button.textContent = before; }, 1500);
  }

  function legacyCopy(text, button) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); } catch (error) { /* selection only */ }
    document.body.removeChild(area);
    label(button);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest
      ? event.target.closest("[data-copy-secret],[data-copy-target],[data-print]")
      : null;
    if (!button) return;
    if (button.hasAttribute("data-print")) {
      window.print();
      return;
    }
    var text = "";
    if (button.hasAttribute("data-copy-secret")) {
      text = button.getAttribute("data-secret") || "";
    } else {
      var target = document.getElementById(button.getAttribute("data-copy-target") || "");
      text = target ? target.textContent.trim() : "";
    }
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { label(button); }, function () {
        legacyCopy(text, button);
      });
    } else {
      legacyCopy(text, button);
    }
  });
})();
