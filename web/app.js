/* ============================================================
   Eden.tools Downloader — front-end logic
   Talks to the Python backend via window.pywebview.api
   ============================================================ */
(function () {
  "use strict";

  var S = {
    view: "new",
    url: "",
    detected: null,
    playlistMode: null,     // null | 'all' | 'single'
    format: "video",        // 'video' | 'audio'
    quality: "best",        // 'best' | 'good' | 'small'
    folder: "Downloads",
    trimOn: true,
    duration: 0,
    start: 0,
    end: 0,
    userTrim: false,
    segments: [],          // [{start,end}] fuer Mehrfach-Zuschnitt
    clipMode: "separate",  // 'separate' = Einzel-Clips | 'remove' = Bereiche entfernen
    queue: [],
    history: [],
    settings: { openAfter: true, auto: true },
    pollTimer: null
  };

  var api = null;
  var drag = null, trackRect = null;

  // ---------- helpers ----------
  function $(id) { return document.getElementById(id); }
  function show(el, on) { if (el) el.classList.toggle("hidden", !on); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmt(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var m = Math.floor(sec / 60), s = sec % 60, h = Math.floor(m / 60);
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    if (h > 0) return h + ":" + pad(m % 60) + ":" + pad(s);
    return m + ":" + pad(s);
  }
  function thumb(hue) {
    hue = hue || 200;
    return "linear-gradient(135deg,hsl(" + hue + " 42% 32%),hsl(" + ((hue + 40) % 360) + " 48% 22%))";
  }
  function toast(msg) {
    var t = $("toast"); if (!t) return;
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._t); t._t = setTimeout(function () { t.classList.remove("show"); }, 2800);
  }

  // ---------- api readiness ----------
  function whenReady(cb) {
    if (window.pywebview && window.pywebview.api) { api = window.pywebview.api; cb(); }
    else window.addEventListener("pywebviewready", function () { api = window.pywebview.api; cb(); }, { once: true });
  }

  // ---------- navigation ----------
  var VIEWS = ["new", "queue", "history", "settings"];
  function setView(v) {
    S.view = v;
    VIEWS.forEach(function (x) {
      $("nav-" + x).classList.toggle("active", x === v);
      show($("view-" + x), x === v);
    });
    if (v === "queue") renderQueue();
    if (v === "history") renderHistory();
  }

  // ---------- detection ----------
  async function doPaste() {
    var txt = "";
    try { txt = await navigator.clipboard.readText(); } catch (e) { txt = ""; }
    txt = (txt || "").trim();
    if (!txt || !/^https?:\/\//i.test(txt)) txt = ($("url-input").value || "").trim();
    if (!txt) { toast("Kein Link gefunden. Bitte oben einfügen (Strg+V) und Enter."); $("url-input").focus(); return; }
    $("url-input").value = txt;
    detect(txt);
  }

  async function detect(url) {
    S.url = url;
    show($("empty-state"), false);
    show($("detected"), false);
    show($("detecting"), true);
    if (!api) { show($("detecting"), false); toast("Backend noch nicht bereit."); return; }
    var d;
    try { d = await api.detect(url); } catch (e) { d = { ok: false, error: String(e) }; }
    show($("detecting"), false);
    if (!d || !d.ok) {
      toast("Konnte den Link nicht lesen: " + ((d && d.error) || "Fehler"));
      show($("empty-state"), true);
      return;
    }
    S.detected = d;
    S.playlistMode = d.isPlaylist ? null : "single";
    S.duration = d.duration || 0;
    S.userTrim = false;
    S.segments = [];
    S.start = Math.round(S.duration * 0.22);
    S.end = Math.round(S.duration * 0.65);
    renderDetected();
  }

  function renderDetected() {
    var d = S.detected;
    show($("detected"), true);
    $("detected-title").textContent = d.title || "(ohne Titel)";
    $("detected-meta").textContent =
      (d.channel ? d.channel + " · " : "") + (d.isPlaylist ? (d.count + " Videos") : fmt(d.duration));

    var needChoice = d.isPlaylist && S.playlistMode === null;
    show($("playlist-banner"), needChoice);
    $("pl-count").textContent = d.count || 0;

    var showFlow = !d.isPlaylist || S.playlistMode !== null;
    show($("flow"), showFlow);

    var showPill = d.isPlaylist && S.playlistMode !== null;
    show($("mode-pill"), showPill);
    if (showPill)
      $("mode-pill-label").textContent =
        S.playlistMode === "all" ? ("Ganze Playlist · " + d.count + " Videos") : "Nur dieses Video";

    var wantPreview = showFlow && (!d.isPlaylist || S.playlistMode === "single");
    setupPreview(wantPreview);
    updateTrimUI();
    updateDownloadLabel();
  }

  function clearUrl() {
    stopPreview();
    S.detected = null; S.playlistMode = null; S.url = ""; S.segments = [];
    $("url-input").value = "";
    show($("detected"), false);
    show($("empty-state"), true);
  }

  // ---------- preview player ----------
  function stopPreview() {
    var v = $("preview-video");
    try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) {}
  }

  function setupPreview(want) {
    show($("player"), want);
    if (!want) { stopPreview(); return; }
    var v = $("preview-video");
    show($("player-loading"), true);
    if (!api) { show($("player-loading"), false); return; }
    api.get_preview(S.url).then(function (r) {
      show($("player-loading"), false);
      if (r && r.ok && r.src) {
        if (r.duration) S.duration = r.duration;
        v.src = r.src;
        try { v.load(); } catch (e) {}
        positionScrub();
      } else {
        toast("Vorschau nicht verfügbar – Zeiten kannst du trotzdem setzen.");
      }
    }).catch(function () { show($("player-loading"), false); });
  }

  function pct(x) { if (!S.duration) return 0; return Math.max(0, Math.min(100, x / S.duration * 100)); }

  function positionScrub() {
    var v = $("preview-video");
    var ph = (v && isFinite(v.currentTime)) ? v.currentTime : 0;
    $("scrub-playhead").style.left = pct(ph) + "%";
    $("scrub-fill").style.width = pct(ph) + "%";
    $("scrub-trim").style.left = pct(S.start) + "%";
    $("scrub-trim").style.width = Math.max(0, pct(S.end) - pct(S.start)) + "%";
    $("handle-start").style.left = pct(S.start) + "%";
    $("handle-end").style.left = pct(S.end) + "%";
    $("start-label").textContent = fmt(S.start);
    $("end-label").textContent = fmt(S.end);
    $("ph-label").textContent = fmt(ph);
    $("dur-label").textContent = fmt(S.duration);
  }

  function updateTrimUI() {
    var on = S.trimOn;
    $("trim-toggle").classList.toggle("on", on);
    show($("scrub-trim"), on);
    show($("handle-start"), on);
    show($("handle-end"), on);
    show($("trim-right"), on);
    show($("seg-panel"), on);
    renderSegments();
    positionScrub();
  }

  // ---------- Mehrfach-Zuschnitt ----------
  function effectiveSegs() {
    if (S.segments.length) return S.segments.slice();
    if (S.trimOn && S.end > S.start) return [{ start: S.start, end: S.end }];
    return [];
  }

  function renderSegments() {
    // Chips
    var list = $("seg-list");
    if (list) {
      list.innerHTML = S.segments.map(function (sg, i) {
        return '<span class="seg-chip">' + (i + 1) + ". " + fmt(sg.start) + "–" + fmt(sg.end) +
          ' <span class="seg-x" data-seg="' + i + '" title="Entfernen">✕</span></span>';
      }).join("");
      list.querySelectorAll(".seg-x").forEach(function (x) {
        x.addEventListener("click", function () {
          S.segments.splice(parseInt(x.getAttribute("data-seg"), 10), 1);
          renderSegments(); updateDownloadLabel();
        });
      });
    }
    // Modus-Buttons
    $("segmode-separate").classList.toggle("on", S.clipMode === "separate");
    $("segmode-remove").classList.toggle("on", S.clipMode === "remove");
    // Hinweistext
    var hint = $("seg-hint");
    if (hint) {
      if (S.clipMode === "remove") {
        hint.textContent = S.segments.length
          ? "Diese Bereiche werden entfernt; der Rest wird zu EINEM Video zusammengefügt."
          : "Markiere oben einen Bereich und füge ihn mit + Bereich hinzu — markierte Bereiche werden aus dem Video herausgeschnitten.";
      } else {
        hint.textContent = S.segments.length
          ? "Jeder Bereich wird als eigene Datei gespeichert."
          : "Ohne + Bereich wird der aktuell markierte Bereich als ein Clip gespeichert.";
      }
    }
    // Bänder auf dem Scrubber
    var track = $("pv-track");
    if (track) {
      track.querySelectorAll(".scrub-seg").forEach(function (n) { n.remove(); });
      var ref = $("scrub-fill");
      S.segments.forEach(function (sg) {
        var b = document.createElement("div");
        b.className = "scrub-seg";
        b.style.left = pct(sg.start) + "%";
        b.style.width = Math.max(0, pct(sg.end) - pct(sg.start)) + "%";
        track.insertBefore(b, ref);
      });
    }
  }

  // ---------- format / quality ----------
  function setFormat(f) {
    S.format = f;
    $("fmt-video").classList.toggle("on", f === "video");
    $("fmt-audio").classList.toggle("on", f === "audio");
  }
  function updateQualityUI() {
    $("quality-label").textContent =
      S.quality === "best" ? "Beste" : S.quality === "good" ? "Gut · 720p" : "Platzsparend";
    document.querySelectorAll(".q-opt").forEach(function (opt) {
      var on = opt.getAttribute("data-q") === S.quality;
      opt.classList.toggle("on", on);
      opt.querySelector(".q-check").classList.toggle("hidden", !on);
    });
  }

  // ---------- download label ----------
  function updateDownloadLabel() {
    var d = S.detected, label = "Herunterladen";
    if (d) {
      var single = !d.isPlaylist || S.playlistMode === "single";
      if (S.trimOn && single) {
        var n = effectiveSegs().length;
        if (S.clipMode === "remove") label = "Bereiche entfernen & laden";
        else label = n > 1 ? (n + " Clips herunterladen") : "Ausschnitt herunterladen";
      } else if (d.isPlaylist && S.playlistMode === "all") {
        label = "Playlist herunterladen";
      }
    }
    $("download-label").textContent = label;
  }

  // ---------- start download ----------
  async function startDownload() {
    if (!api) { toast("Backend noch nicht bereit."); return; }
    if (!S.detected) {
      // Link steht im Feld, wurde aber (noch) nicht erkannt -> jetzt erkennen
      var u = ($("url-input").value || "").trim();
      if (u) detect(u);
      else toast("Bitte zuerst einen Link einfügen.");
      return;
    }
    var d = S.detected;
    var single = !d.isPlaylist || S.playlistMode === "single";
    var trim = (S.trimOn && single) ? { on: true, start: S.start, end: S.end } : { on: false };
    var params = {
      url: S.url, title: d.title, count: d.count || 0,
      isPlaylist: !!d.isPlaylist, playlistMode: S.playlistMode,
      format: S.format, quality: S.quality, trim: trim, duration: S.duration,
      segments: (S.trimOn && single) ? effectiveSegs() : [],
      clipMode: S.clipMode
    };
    $("download-btn").disabled = true;
    try { await api.start_download(params); } catch (e) { toast("Start fehlgeschlagen."); }
    $("download-btn").disabled = false;
    setView("queue");
    startPolling();
  }

  // ---------- polling queue/history ----------
  function startPolling() {
    if (S.pollTimer) return;
    pollOnce();
    S.pollTimer = setInterval(pollOnce, 600);
  }
  async function pollOnce() {
    if (!api) return;
    var st;
    try { st = await api.get_state(); } catch (e) { return; }
    applyState(st);
    var active = (st.queue || []).some(function (q) { return q.status === "downloading" || q.status === "queued"; });
    if (!active && S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  }

  function applyState(st) {
    if (!st) return;
    if (st.folder) {
      S.folder = st.folder;
      $("folder-path").textContent = st.folder;
      $("set-folder-path").textContent = st.folder;
    }
    if (st.settings) {
      S.settings = st.settings;
      $("toggle-openafter").classList.toggle("on", !!st.settings.openAfter);
      $("toggle-auto").classList.toggle("on", !!st.settings.auto);
    }
    S.queue = st.queue || [];
    S.history = st.history || [];
    renderQueue(); renderHistory(); updateBadge();
  }

  function updateBadge() {
    var active = S.queue.filter(function (q) { return q.status === "downloading" || q.status === "queued"; }).length;
    show($("queue-badge"), active > 0);
    $("queue-badge").textContent = active;
  }

  function renderQueue() {
    var list = $("queue-list");
    show($("queue-empty"), S.queue.length === 0);
    list.innerHTML = S.queue.map(function (it) {
      var p = Math.round(it.progress || 0);
      var statusLabel = it.status === "done" ? "Fertig"
        : it.status === "error" ? "Fehler"
        : it.status === "queued" ? "Wartet" : (p + " %");
      var statusColor = it.status === "done" ? "#1f9d57" : it.status === "error" ? "#c0392b" : "var(--accent)";
      var barColor = it.status === "done" ? "#22c06a" : "var(--accent)";
      return '<div class="q-card">' +
        '<div class="q-thumb" style="background:' + thumb(it.hue) + '"><svg width="18" height="18" viewBox="0 0 24 24" fill="rgba(255,255,255,.9)"><path d="M8 5v14l11-7z"/></svg></div>' +
        '<div class="q-main"><div class="q-top"><div class="q-title">' + esc(it.title) + '</div>' +
        '<div class="q-status" style="color:' + statusColor + '">' + statusLabel + '</div></div>' +
        '<div class="q-sub">' + esc(it.sub || "") + '</div>' +
        '<div class="q-bar"><div style="width:' + p + '%;background:' + barColor + '"></div></div></div>' +
        '<button class="icon-btn" data-remove="' + esc(it.id) + '"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
        '</div>';
    }).join("");
    list.querySelectorAll("[data-remove]").forEach(function (b) {
      b.addEventListener("click", function () { if (api) api.cancel(b.getAttribute("data-remove")); pollOnce(); });
    });
  }

  function renderHistory() {
    var list = $("history-list");
    show($("history-empty"), S.history.length === 0);
    list.innerHTML = S.history.map(function (it) {
      return '<div class="h-card">' +
        '<div class="h-thumb" style="background:' + thumb(it.hue) + '"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c06a" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-11"/></svg></div>' +
        '<div class="h-main"><div class="h-title">' + esc(it.title) + '</div>' +
        '<div class="h-sub">' + esc(it.sub || "") + (it.when ? " · " + esc(it.when) : "") + '</div></div>' +
        '<button class="btn-outline" data-open="' + esc(it.filepath || "") + '">Ordner öffnen</button>' +
        '</div>';
    }).join("");
    list.querySelectorAll("[data-open]").forEach(function (b) {
      b.addEventListener("click", function () { if (api) api.open_folder(b.getAttribute("data-open")); });
    });
  }

  // ---------- folder ----------
  async function chooseFolder() {
    if (!api) return;
    var r;
    try { r = await api.choose_folder(); } catch (e) { return; }
    if (r && r.folder) {
      S.folder = r.folder;
      $("folder-path").textContent = r.folder;
      $("set-folder-path").textContent = r.folder;
    }
  }

  // ---------- wire up ----------
  function wire() {
    VIEWS.forEach(function (v) { $("nav-" + v).addEventListener("click", function () { setView(v); }); });

    // window controls (frameless titlebar)
    $("tb-min").addEventListener("click", function () { if (api && api.win_minimize) api.win_minimize(); });
    $("tb-max").addEventListener("click", function () { if (api && api.win_maximize) api.win_maximize(); });
    $("tb-close").addEventListener("click", function () { if (api && api.win_close) api.win_close(); });

    $("paste-btn").addEventListener("click", doPaste);
    $("url-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { var u = $("url-input").value.trim(); if (u) detect(u); }
    });
    $("ex-video").addEventListener("click", doPaste);
    $("ex-playlist").addEventListener("click", doPaste);
    $("clear-btn").addEventListener("click", clearUrl);

    $("choose-all").addEventListener("click", function () { S.playlistMode = "all"; renderDetected(); });
    $("choose-single").addEventListener("click", function () { S.playlistMode = "single"; renderDetected(); });
    $("mode-change").addEventListener("click", function () { S.playlistMode = null; renderDetected(); });

    // player
    var v = $("preview-video");
    $("play-btn").addEventListener("click", function () {
      if (v.paused) { v.play().catch(function () {}); } else { v.pause(); }
    });
    v.addEventListener("play", function () { show($("ic-play"), false); show($("ic-pause"), true); });
    v.addEventListener("pause", function () { show($("ic-play"), true); show($("ic-pause"), false); });
    v.addEventListener("timeupdate", positionScrub);
    v.addEventListener("loadedmetadata", function () {
      if (v.duration && isFinite(v.duration)) {
        S.duration = v.duration;
        if (!S.userTrim) { S.start = Math.round(S.duration * 0.22); S.end = Math.round(S.duration * 0.65); }
      }
      updateTrimUI();
    });

    $("pv-track").addEventListener("click", function (e) {
      if (drag) return;
      var r = $("pv-track").getBoundingClientRect();
      var p = (e.clientX - r.left) / r.width; p = Math.max(0, Math.min(1, p));
      if (S.duration && isFinite(v.duration)) v.currentTime = p * S.duration;
      positionScrub();
    });
    $("handle-start").addEventListener("pointerdown", function (e) { beginDrag("start", e); });
    $("handle-end").addEventListener("pointerdown", function (e) { beginDrag("end", e); });
    $("handle-start").addEventListener("click", function (e) { e.stopPropagation(); });
    $("handle-end").addEventListener("click", function (e) { e.stopPropagation(); });

    $("trim-toggle").addEventListener("click", function () { S.trimOn = !S.trimOn; updateTrimUI(); updateDownloadLabel(); });
    $("set-start").addEventListener("click", function () {
      S.start = Math.min(Math.round(v.currentTime), S.end - 5); if (S.start < 0) S.start = 0; S.userTrim = true; positionScrub();
    });
    $("set-end").addEventListener("click", function () {
      S.end = Math.max(Math.round(v.currentTime), S.start + 5); S.userTrim = true; positionScrub();
    });
    $("add-seg").addEventListener("click", function () {
      if (S.end > S.start) {
        S.segments.push({ start: Math.round(S.start), end: Math.round(S.end) });
        renderSegments(); updateDownloadLabel();
      }
    });
    $("segmode-separate").addEventListener("click", function () { S.clipMode = "separate"; renderSegments(); updateDownloadLabel(); });
    $("segmode-remove").addEventListener("click", function () { S.clipMode = "remove"; renderSegments(); updateDownloadLabel(); });

    // format / quality
    $("fmt-video").addEventListener("click", function () { setFormat("video"); });
    $("fmt-audio").addEventListener("click", function () { setFormat("audio"); });
    $("quality-btn").addEventListener("click", function (e) {
      e.stopPropagation();
      show($("quality-menu"), $("quality-menu").classList.contains("hidden"));
    });
    document.querySelectorAll(".q-opt").forEach(function (opt) {
      opt.addEventListener("click", function () { S.quality = opt.getAttribute("data-q"); updateQualityUI(); show($("quality-menu"), false); });
    });
    document.addEventListener("click", function (e) {
      var q = $("quality"); if (q && !q.contains(e.target)) show($("quality-menu"), false);
    });

    // folder + download
    $("change-folder").addEventListener("click", chooseFolder);
    $("set-change-folder").addEventListener("click", chooseFolder);
    $("download-btn").addEventListener("click", startDownload);

    // settings toggles
    $("toggle-openafter").addEventListener("click", function () {
      var on = !$("toggle-openafter").classList.contains("on");
      $("toggle-openafter").classList.toggle("on", on);
      if (api) api.set_settings("openAfter", on);
    });
    $("toggle-auto").addEventListener("click", function () {
      var on = !$("toggle-auto").classList.contains("on");
      $("toggle-auto").classList.toggle("on", on);
      if (api) api.set_settings("auto", on);
    });

    // global drag for trim handles
    document.addEventListener("pointermove", function (e) {
      if (!drag || !trackRect) return;
      var p = (e.clientX - trackRect.left) / trackRect.width; p = Math.max(0, Math.min(1, p));
      var sec = Math.round(p * S.duration);
      if (drag === "start") S.start = Math.max(0, Math.min(sec, S.end - 5));
      else S.end = Math.max(sec, S.start + 5);
      S.userTrim = true;
      positionScrub();
    });
    document.addEventListener("pointerup", function () { drag = null; });
  }

  function beginDrag(which, e) {
    e.preventDefault(); e.stopPropagation();
    drag = which;
    trackRect = $("pv-track").getBoundingClientRect();
  }

  // ---------- updates ----------
  var U = { timer: null, last: null };
  function renderUpdate(u) {
    U.last = u;
    var card = $("status-card"), line = $("status-text"), sub = $("status-sub");
    var btn = $("upd-btn"), bar = $("upd-bar");
    var ver = "v" + u.app + (u.ytdlp ? " · yt-dlp " + u.ytdlp : "") + (u.ytdlpNeu ? " (neuer nach Neustart)" : "");
    card.className = "status-card"; card.title = "";
    show(btn, false); show(bar, false);
    sub.textContent = ver;
    if (u.phase === "laden") {
      card.classList.add("upd");
      line.textContent = "Update lädt … " + (u.fortschritt || 0) + " %";
      show(bar, true); $("upd-fill").style.width = (u.fortschritt || 0) + "%";
    } else if (u.phase === "neustart") {
      card.classList.add("upd"); line.textContent = "Startet neu …";
    } else if (u.phase === "fehler") {
      card.classList.add("err"); line.textContent = "Update fehlgeschlagen";
      sub.textContent = u.fehler || ver;
      if (u.neu) { btn.textContent = "Nochmal versuchen"; show(btn, true); }
    } else if (u.neu) {
      card.classList.add("upd"); card.title = u.neu.notes || "";
      line.textContent = "Update " + u.neu.version;
      btn.textContent = u.tauschbar ? "Jetzt aktualisieren" : "Herunterladen";
      show(btn, true);
    } else if (u.phase === "pruefen") {
      card.classList.add("busy"); line.textContent = "Suche Updates …";
    } else {
      line.textContent = "Alles bereit";
    }
    $("set-version").textContent = ver + (u.neu ? " · " + u.neu.version + " verfügbar" : "");
    var busy = u.phase === "pruefen" || u.phase === "laden" || u.phase === "neustart";
    clearTimeout(U.timer);
    U.timer = setTimeout(pollUpdate, busy ? 500 : 60000);
  }
  async function pollUpdate() {
    if (!api) return;
    try { renderUpdate(await api.update_status()); } catch (e) {}
  }
  async function installUpdate() {
    var u = U.last; if (!api || !u || !u.neu) return;
    if (!u.tauschbar) { api.open_url(u.neu.seite); return; }
    try { await api.update_install(); } catch (e) {}
    pollUpdate();
  }

  // ---------- boot ----------
  document.addEventListener("DOMContentLoaded", function () {
    wire();
    updateQualityUI();
    $("upd-btn").addEventListener("click", installUpdate);
    $("set-check").addEventListener("click", async function () {
      if (!api) return;
      try { await api.update_check(); } catch (e) {}
      setTimeout(pollUpdate, 150);
    });
    whenReady(async function () {
      try { var st = await api.get_state(); applyState(st); } catch (e) {}
      try { await api.update_check(); } catch (e) {}
      pollUpdate();
    });
  });
})();
