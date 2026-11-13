function initSearch(){
  const q=document.getElementById("q"),body=document.getElementById("results"),empty=document.getElementById("empty");
  if(!q||!body)return;
  let t;
  async function run(){
    const r=await fetch(q.dataset.url+"?q="+encodeURIComponent(q.value));
    const {results}=await r.json();
    body.innerHTML="";empty.hidden=results.length>0;
    results.forEach(s=>{
      const tr=document.createElement("tr"),owing=parseFloat(s.balance)>0;
      const cells=[s.no,null,s.programme,s.status,CUR+s.balance];
      cells.forEach((c,i)=>{const td=document.createElement("td");
        if(i===1){const a=document.createElement("a");a.href=BASE+s.id+"/";a.textContent=s.name;td.appendChild(a)}
        else td.textContent=c;
        if(i===4){td.className="r";td.style.color=owing?"var(--bad)":"var(--ok)";td.style.fontWeight="600"}
        tr.appendChild(td)});
      body.appendChild(tr)});
  }
  q.addEventListener("input",()=>{clearTimeout(t);t=setTimeout(run,200)});run();
}

/* ------------------------------------------------------------------
   Shell enhancements: mobile drawer, active navigation, table wrappers.
   Nothing here changes what a form, link or button does.
   ------------------------------------------------------------------ */
(function () {
  function markActiveNav() {
    const links = Array.from(document.querySelectorAll(".side nav a"));
    if (!links.length) return;
    const here = location.pathname.replace(/\/+$/, "") || "/";
    let best = null;
    let bestLength = -1;
    links.forEach(function (link) {
      let path;
      try {
        path = new URL(link.href, location.origin).pathname.replace(/\/+$/, "") || "/";
      } catch (error) {
        return;
      }
      const matches = path === "/" ? here === "/" : here === path || here.indexOf(path + "/") === 0;
      if (matches && path.length > bestLength) {
        best = link;
        bestLength = path.length;
      }
    });
    if (best) {
      best.classList.add("active");
      best.setAttribute("aria-current", "page");
    }
  }

  function wrapTables() {
    document.querySelectorAll("table.tbl").forEach(function (table) {
      const parent = table.parentElement;
      if (!parent || parent.classList.contains("tbl-wrap")) return;
      if (getComputedStyle(parent).overflowX !== "visible") return;
      if (table.closest(".tbl-wrap")) return;
      const wrapper = document.createElement("div");
      wrapper.className = "tbl-wrap";
      parent.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    });
  }

  function setUpDrawer() {
    const toggle = document.getElementById("menuToggle");
    const side = document.getElementById("side");
    const overlay = document.getElementById("sideOverlay");
    if (!toggle || !side) return;

    function setOpen(open) {
      side.classList.toggle("open", open);
      if (overlay) overlay.classList.toggle("show", open);
      document.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }

    toggle.addEventListener("click", function () {
      setOpen(!side.classList.contains("open"));
    });

    if (overlay) {
      overlay.addEventListener("click", function () {
        setOpen(false);
      });
    }

    side.querySelectorAll("nav a").forEach(function (link) {
      link.addEventListener("click", function () {
        if (window.matchMedia("(max-width: 1024px)").matches) setOpen(false);
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setOpen(false);
    });

    window.addEventListener("resize", function () {
      if (window.innerWidth > 1024) setOpen(false);
    });
  }

  function chartDefaults() {
    if (!window.Chart) return;
    window.Chart.defaults.font.family =
      "'Figtree', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
    window.Chart.defaults.color = "#64748B";
    window.Chart.defaults.borderColor = "#E2E8F0";
  }

  document.addEventListener("DOMContentLoaded", function () {
    markActiveNav();
    wrapTables();
    setUpDrawer();
    chartDefaults();
  });
})();
