// Soulbridge client scripts (kept external so the CSP can forbid inline JS).
(function () {
  "use strict";

  // --- live status strip (all pages) ---
  function pill(id, state) {   // true=ok, false=err, null/undefined=muted
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("ok", "err", "muted");
    el.classList.add(state === true ? "ok" : state === false ? "err" : "muted");
  }
  async function pollStatus() {
    if (document.hidden || !document.getElementById("sb-slskd")) return;
    try {
      var r = await fetch("/api/status", { cache: "no-store" });
      if (!r.ok) return;
      var w = (await r.json()).worker || {};
      var dot = document.getElementById("sb-worker");
      if (dot) { dot.classList.toggle("on", !!w.running); dot.classList.toggle("off", !w.running); }
      pill("sb-slskd", !!w.slskd_connected);
      pill("sb-abr", w.abr_connected ? true : null);
      pill("sb-abs", w.abs_connected);
      pill("sb-plex", w.plex_connected);
      pill("sb-jellyfin", w.jellyfin_connected);
      var p = document.getElementById("sb-poll");
      if (p && w.last_poll) p.textContent = w.last_poll;
    } catch (e) { /* transient */ }
  }

  // --- dashboard body auto-refresh ---
  async function refreshDash() {
    var el = document.getElementById("dash");
    if (!el || document.hidden) return;
    try {
      var r = await fetch("/partials/dashboard", { cache: "no-store" });
      if (r.ok) el.innerHTML = await r.text();
    } catch (e) { /* transient */ }
  }

  if (document.getElementById("sb-slskd")) setInterval(pollStatus, 5000);
  if (document.getElementById("dash")) setInterval(refreshDash, 5000);

  // --- server settings: dirty-glow save button + side-nav scrollspy ---
  var form = document.getElementById("settingsform");
  if (form) {
    var save = document.getElementById("savebtn");
    var els = Array.from(form.elements).filter(function (e) { return e.name && e.name !== "csrf"; });
    var initial = new Map();
    els.forEach(function (e) { initial.set(e, e.type === "checkbox" ? e.checked : e.value); });
    function recompute() {
      var dirty = els.some(function (e) {
        return (e.type === "checkbox" ? e.checked : e.value) !== initial.get(e);
      });
      if (save) save.classList.toggle("dirty", dirty);
    }
    form.addEventListener("input", recompute);
    form.addEventListener("change", recompute);

    var links = Array.from(document.querySelectorAll(".settingsnav a"));
    var byId = {};
    links.forEach(function (a) { byId[a.dataset.target] = a; });
    if ("IntersectionObserver" in window && links.length) {
      var obs = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            links.forEach(function (a) { a.classList.remove("active"); });
            if (byId[en.target.id]) byId[en.target.id].classList.add("active");
          }
        });
      }, { rootMargin: "-40% 0px -55% 0px" });
      form.querySelectorAll("fieldset").forEach(function (fs) { obs.observe(fs); });
    }
  }
})();
