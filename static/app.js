/* PodCache — progressive enhancement only.
   The site works without this file; it adds the theme toggle and live download
   progress. Everything is guarded so a failure can never break the page. */
(function () {
  "use strict";

  // ── Theme toggle ──────────────────────────────────────────────────────────
  try {
    var meta = document.querySelector('meta[name="theme-color"]');
    function applyTheme(mode) {
      if (mode === "light") {
        document.documentElement.setAttribute("data-theme", "light");
        if (meta) meta.setAttribute("content", "#efe6d8");
      } else {
        document.documentElement.removeAttribute("data-theme");
        if (meta) meta.setAttribute("content", "#0f0d0b");
      }
      var t = document.getElementById("themeToggle");
      if (t) t.setAttribute("aria-checked", mode === "light" ? "true" : "false");
    }
    var current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    applyTheme(current);
    var toggle = document.getElementById("themeToggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
        try { localStorage.setItem("theme", next); } catch (e) {}
        applyTheme(next);
      });
    }
  } catch (e) { /* theme is non-critical */ }

  // Fallback image glyph used by server-rendered onerror handlers.
  window.__mic =
    '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/></svg>';

  // ── Episode multi-select: "Select all" + live button label ────────────────
  try {
    var selectAll = document.getElementById("selectAll");
    var cbs = Array.prototype.slice.call(document.querySelectorAll(".ep-cb"));
    if (cbs.length) {
      var btnLabel = document.getElementById("dlBtnLabel");
      var selCount = document.getElementById("selCount");
      function refresh() {
        var n = cbs.filter(function (c) { return c.checked; }).length;
        if (btnLabel) btnLabel.textContent = n ? "Download " + n + " selected" : "Download selected";
        if (selCount) selCount.textContent = n ? n + " selected" : "Select all on page";
        if (selectAll) selectAll.checked = n === cbs.length;
      }
      cbs.forEach(function (c) { c.addEventListener("change", refresh); });
      if (selectAll) {
        selectAll.addEventListener("change", function (e) {
          cbs.forEach(function (c) { c.checked = e.target.checked; });
          refresh();
        });
      }
      refresh();
    }
  } catch (e) { /* selection is non-critical; checkboxes still work */ }

  // ── Live download progress (Downloads page only) ──────────────────────────
  try {
    var list = document.getElementById("dlList");
    var section = document.getElementById("downloadsSection");
    if (!section) return; // not on the downloads page
    if (typeof EventSource === "undefined") return;

    var VERB = { queued: "Queued", downloading: "Downloading", completed: "Completed", failed: "Failed", skipped: "Already saved" };

    function rowHTML(it) {
      var pct = Math.round((it.progress || 0) * 100);
      var indet = it.status === "downloading" && !it.progress;
      var tail = "";
      if (it.status === "failed" && it.error) tail = "<span>· " + escapeHTML(it.error) + "</span>";
      else if ((it.status === "completed" || it.status === "skipped") && it.rel_path)
        tail = '<span>· <a href="/file/' + encodeURIComponent(it.id) + '" download>' + escapeHTML(it.rel_path) + "</a></span>";
      else if (it.status === "downloading") tail = "<span>· " + pct + "%</span>";
      return (
        '<div class="qrow"><span class="qname">' + escapeHTML(it.title) + '</span>' +
        '<span class="qstatus">' + (VERB[it.status] || it.status) + "</span></div>" +
        '<div class="qmeta"><span class="show">' + escapeHTML(it.show) + "</span>" + tail + "</div>" +
        '<div class="progress' + (indet ? " indeterminate" : "") + '"><div class="bar" style="width:' + pct + '%"></div></div>'
      );
    }
    function escapeHTML(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

    function ensureList() {
      if (!list) {
        // Replace an empty-state placeholder with a real list container.
        var es = section.querySelector(".empty-state");
        list = document.createElement("div");
        list.className = "dl-list";
        list.id = "dlList";
        if (es && es.parentNode) es.parentNode.replaceChild(list, es);
        else section.appendChild(list);
      }
      return list;
    }
    function upsert(it) {
      var el = document.getElementById("dl-" + it.id);
      if (!el) {
        el = document.createElement("div");
        el.id = "dl-" + it.id;
        ensureList().appendChild(el);
      }
      el.className = "qitem " + it.status;
      el.setAttribute("data-status", it.status);
      el.innerHTML = rowHTML(it);
    }
    function updateHeading(items) {
      var active = 0;
      for (var k in items) if (items[k].status === "queued" || items[k].status === "downloading") active++;
      var total = Object.keys(items).length;
      var h = document.getElementById("downloadsTitle");
      if (h) h.textContent = total ? (active ? active + " in progress · " + total + " total" : total + (total === 1 ? " download" : " downloads")) : "Queue";
      var tab = document.querySelector('.tabs a[href="/downloads"]');
      if (tab) {
        var c = tab.querySelector(".count");
        if (total && !c) { c = document.createElement("span"); c.className = "count"; tab.appendChild(document.createTextNode(" ")); tab.appendChild(c); }
        if (c) c.textContent = total ? "(" + total + ")" : "";
      }
    }

    var items = {};
    var src = new EventSource("/events");
    src.onmessage = function (e) {
      var ev; try { ev = JSON.parse(e.data); } catch (x) { return; }
      if (ev.type === "snapshot") {
        items = {};
        (ev.items || []).forEach(function (it) { items[it.id] = it; upsert(it); });
        updateHeading(items);
      } else if (ev.type === "item") {
        items[ev.id] = Object.assign(items[ev.id] || {}, ev);
        upsert(items[ev.id]);
        updateHeading(items);
      }
    };
    src.onerror = function () { /* EventSource auto-reconnects; snapshot re-syncs */ };
  } catch (e) { /* live updates are non-critical; the page still shows a snapshot */ }
})();
