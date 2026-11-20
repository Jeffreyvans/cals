/* Vanpere public pages: mobile menu, sticky header, searchable institution
   picker. No framework and no tracking; the page works without JavaScript. */
(function () {
  "use strict";

  function setUpMenu() {
    var toggle = document.querySelector("[data-nav-toggle]");
    var nav = document.getElementById("siteNav");
    if (!toggle || !nav) return;

    function setOpen(open) {
      nav.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }

    toggle.addEventListener("click", function () {
      setOpen(!nav.classList.contains("open"));
    });

    nav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        setOpen(false);
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && nav.classList.contains("open")) {
        setOpen(false);
        toggle.focus();
      }
    });

    window.addEventListener("resize", function () {
      if (window.innerWidth > 760) setOpen(false);
    });
  }

  function setUpHeader() {
    var header = document.querySelector("[data-site-head]");
    if (!header) return;
    function update() {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    }
    update();
    window.addEventListener("scroll", update, { passive: true });
  }

  function setUpSelectSearch() {
    document.querySelectorAll("select[data-searchable]").forEach(function (select) {
      var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
      var search = document.createElement("input");
      search.type = "search";
      search.className = "select-search";
      search.placeholder = "Type to search the list";
      search.setAttribute("aria-label", "Search the list of institutions");
      search.autocomplete = "off";
      if (label && label.id) {
        search.setAttribute("aria-controls", select.id);
      }
      select.parentNode.insertBefore(search, select);

      var options = Array.prototype.slice.call(select.options);
      search.addEventListener("input", function () {
        var term = search.value.trim().toLowerCase();
        options.forEach(function (option) {
          if (!option.value) {
            option.hidden = false;
            return;
          }
          option.hidden = Boolean(term) && option.text.toLowerCase().indexOf(term) === -1;
        });
        if (!term) select.value = select.value;
      });

      select.addEventListener("change", function () {
        if (!search.value) return;
        search.value = "";
        options.forEach(function (option) {
          option.hidden = false;
        });
      });
    });
  }

  function setUpPositionFilter() {
    var form = document.querySelector("[data-position-form]");
    var mapNode = document.getElementById("position-map");
    if (!form || !mapNode) return;
    var institution = form.querySelector("#id_institution");
    var position = form.querySelector("#id_position");
    if (!institution || !position) return;

    var allowedByInstitution;
    try {
      allowedByInstitution = JSON.parse(mapNode.textContent);
    } catch (error) {
      return;
    }

    function update() {
      var allowed = allowedByInstitution[institution.value];
      var firstAllowed = null;
      Array.prototype.forEach.call(position.options, function (option) {
        if (!option.value) return;
        var visible = !allowed || allowed.indexOf(option.value) !== -1;
        option.hidden = !visible;
        option.disabled = !visible;
        if (visible && !firstAllowed) firstAllowed = option;
      });
      if (allowed && position.value && allowed.indexOf(position.value) === -1) {
        position.value = firstAllowed ? firstAllowed.value : "";
      }
    }

    institution.addEventListener("change", update);
    update();
  }

  function setUpTheme() {
    var root = document.documentElement;
    var button = document.querySelector("[data-theme-toggle]");
    // Internal browser-storage key: kept so saved themes keep working.
    var storageKey = "edusphere.public.theme";
    var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

    function stored() {
      try {
        var value = window.localStorage.getItem(storageKey);
        return value === "dark" || value === "light" ? value : "";
      } catch (error) {
        return "";
      }
    }

    function apply(theme) {
      if (theme) {
        root.setAttribute("data-theme", theme);
      } else {
        root.removeAttribute("data-theme");
      }
      if (button) {
        var dark = theme
          ? theme === "dark"
          : Boolean(media && media.matches);
        button.setAttribute("aria-pressed", dark ? "true" : "false");
        button.setAttribute(
          "aria-label",
          dark ? "Switch to light theme" : "Switch to dark theme"
        );
      }
    }

    var current = stored();
    apply(current);
    if (media) {
      var onChange = function () {
        if (!stored()) apply("");
      };
      if (media.addEventListener) media.addEventListener("change", onChange);
      else if (media.addListener) media.addListener(onChange);
    }

    if (!button) return;
    button.addEventListener("click", function () {
      var isDark = root.getAttribute("data-theme")
        ? root.getAttribute("data-theme") === "dark"
        : Boolean(media && media.matches);
      var next = isDark ? "light" : "dark";
      try {
        window.localStorage.setItem(storageKey, next);
      } catch (error) {
        /* storage may be blocked; the choice still applies for this page */
      }
      apply(next);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setUpTheme();
    setUpMenu();
    setUpHeader();
    setUpSelectSearch();
    setUpPositionFilter();
  });
})();

/* Keep the public shell on the device for repeat visits on slow connections. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () { /* offline support is optional */ });
  });
}
