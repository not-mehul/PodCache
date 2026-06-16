/* PodCache — progressive enhancement only.
The site works without this file; it adds the theme toggle, episode-selection
helpers, and live download progress. Every part is guarded. */
(function () {
  "use strict";

  // Fallback glyph for broken images (used by server-rendered onerror handlers).
  window.__mic =
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/></svg>';

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
      var t = document.getElementById("theme-toggle");
      if (t)
        t.setAttribute("aria-checked", mode === "light" ? "true" : "false");
    }
    applyTheme(
      document.documentElement.getAttribute("data-theme") === "light"
        ? "light"
        : "dark",
    );
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var next =
          document.documentElement.getAttribute("data-theme") === "light"
            ? "dark"
            : "light";
        try {
          localStorage.setItem("theme", next);
        } catch (e) {}
        applyTheme(next);
      });
    }
  } catch (e) {
    /* theme is non-critical */
  }

  // ── Episode selection helpers ─────────────────────────────────────────────
  try {
    var cbs = Array.prototype.slice.call(document.querySelectorAll(".ep-cb"));
    if (cbs.length) {
      var btnLabel = document.getElementById("dlBtnLabel");
      var metaSelected = document.getElementById("metaSelected");
      var selectPage = document.getElementById("selectPage");
      var clearSel = document.getElementById("clearSel");
      var downloadBtn = document.getElementById("downloadBtn");
      function refresh() {
        var n = cbs.filter(function (c) {
          return c.checked;
        }).length;
        if (btnLabel)
          btnLabel.textContent = n
            ? "Download " + n + " selected"
            : "Download selected";
        if (metaSelected) metaSelected.textContent = String(n);
        if (downloadBtn) downloadBtn.disabled = n === 0;
      }
      cbs.forEach(function (c) {
        c.addEventListener("change", refresh);
      });
      if (selectPage)
        selectPage.addEventListener("click", function () {
          cbs.forEach(function (c) {
            c.checked = true;
          });
          refresh();
        });
      if (clearSel)
        clearSel.addEventListener("click", function () {
          cbs.forEach(function (c) {
            c.checked = false;
          });
          refresh();
        });
      refresh();
    }
  } catch (e) {
    /* selection is non-critical; checkboxes still work */
  }

  // ── Live download progress (Downloads page only) ──────────────────────────
  try {
    if (!document.getElementById("downloadsTitle")) return; // not the downloads page
    if (typeof EventSource === "undefined") return;

    var VERB = {
      queued: "Queued",
      downloading: "Downloading",
      completed: "Completed",
      failed: "Failed",
      skipped: "Already saved",
    };
    function escapeHTML(s) {
      var d = document.createElement("div");
      d.textContent = s == null ? "" : s;
      return d.innerHTML;
    }

    function thumbHTML(it) {
      if (it.image)
        return (
          '<div class="qthumb"><img src="' +
          escapeHTML(it.image) +
          '" alt="" loading="lazy" onerror="this.parentNode.innerHTML=window.__mic" /></div>'
        );
      return '<div class="qthumb">' + window.__mic + "</div>";
    }

    function bodyHTML(it) {
      var pct = Math.round((it.progress || 0) * 100);
      var indet = it.status === "downloading" && !it.progress;
      var chips = "";
      if (it.status === "failed" && it.error)
        chips = '<span class="qchip mono">' + escapeHTML(it.error) + "</span>";
      else if (
        (it.status === "completed" || it.status === "skipped") &&
        it.rel_path
      )
        chips =
          '<a href="/file/' +
          encodeURIComponent(it.id) +
          '" download>' +
          escapeHTML(it.rel_path) +
          "</a>";
      else if (it.status === "downloading")
        chips = '<span class="qchip mono">' + pct + "%</span>";
      return (
        '<div class="qbody"><p class="qtitle">' +
        escapeHTML(it.title) +
        "</p>" +
        '<div class="qmeta"><span class="status-verb">' +
        (VERB[it.status] || it.status) +
        "</span>" +
        '<span class="qchip">' +
        escapeHTML(it.show) +
        "</span>" +
        chips +
        "</div>" +
        '<div class="qprogress' +
        (indet ? " indeterminate" : "") +
        '"><div class="bar" style="width:' +
        pct +
        '%"></div></div></div>'
      );
    }

    var section = document.querySelector(".section");

    function ensureList() {
      var list = document.querySelector(".queue-list");
      if (!list) {
        var es = document.querySelector(".empty-state");
        list = document.createElement("div");
        list.className = "queue-list";
        if (es && es.parentNode) es.parentNode.replaceChild(list, es);
        else if (section) section.appendChild(list);
      }
      return list;
    }
    function upsert(it) {
      var el = document.getElementById("dl-" + it.id);
      if (!el) {
        var list = ensureList();
        if (!list) return;
        el = document.createElement("div");
        el.id = "dl-" + it.id;
        list.appendChild(el);
      }
      el.className = "qitem " + it.status;
      el.setAttribute("data-status", it.status);
      el.innerHTML = thumbHTML(it) + bodyHTML(it);
    }
    function updateHeading(items) {
      var total = Object.keys(items).length,
        active = 0;
      for (var k in items)
        if (items[k].status === "queued" || items[k].status === "downloading")
          active++;
      var h = document.getElementById("downloadsTitle");
      if (h)
        h.textContent = total
          ? active
            ? active + " in progress · " + total + " total"
            : total + (total === 1 ? " download" : " downloads")
          : "Queue";
      var tab = document.querySelector('.tabs a[href="/downloads"]');
      if (tab) {
        var c = tab.querySelector(".count");
        if (total && !c) { tab.appendChild(document.createTextNode(" ")); c = document.createElement("span"); c.className = "count"; tab.appendChild(c); }
        }
        if (c) c.textContent = total ? "(" + total + ")" : "";
      }
    }

    var items = {};
    var src = new EventSource("/events");
    src.onmessage = function (e) {
      var ev;
      try {
        ev = JSON.parse(e.data);
      } catch (x) {
        return;
      }
      if (ev.type === "snapshot") {
        items = {};
        (ev.items || []).forEach(function (it) {
          items[it.id] = it;
          upsert(it);
        });
        updateHeading(items);
      } else if (ev.type === "item") {
        items[ev.id] = Object.assign(items[ev.id] || {}, ev);
        upsert(items[ev.id]);
        updateHeading(items);
      }
    };
    src.onerror = function () { /* auto-reconnects; snapshot re-syncs */ };
  } catch (e) {
    /* live updates are non-critical; the page still shows a snapshot */
  }
})();
