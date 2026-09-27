#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Eden.tools Downloader
=====================
Moderner Video-Downloader (YouTube, Vimeo, TikTok ...) mit Web-UI in einem
nativen Fenster (pywebview) und einem Python-Backend auf Basis von
yt-dlp + ffmpeg.

Architektur:
  - web/            -> die Oberflaeche (HTML/CSS/JS), gerendert via pywebview
  - eden_downloader.py (diese Datei) -> Bootstrap + Api-Bruecke zum Backend

Die schwere Logik (yt-dlp/ffmpeg) ist aus dem urspruenglichen Tool
uebernommen und wird ueber die `Api`-Klasse fuer das JS verfuegbar gemacht.

Python 3.11 / 3.12 / 3.13 empfohlen.
"""

import os
import re
import sys
import json
import time
import shutil
import tempfile
import threading
import subprocess

import webview

import aktualisierung

APP_NAME = "Eden.tools Downloader"
APP_VERSION = "1.1.1"

# Vor jedem yt_dlp-Import: selbst geholte yt-dlp-Fassungen vorziehen, alte EXE vom letzten Update weg.
aktualisierung.reste_wegraeumen()
aktualisierung.pakete_einhaengen()

# itag 18 = 360p MP4 (Bild + Ton in EINER Datei) -> ideale, kleine Vorschau.
# YouTube liefert 18 inzwischen oft nicht mehr (nur noch getrennte Spuren),
# dann H.264 + AAC bis 480p holen und per ffmpeg zu einer MP4 zusammenfuegen.
PREVIEW_FORMAT = ("18/bv*[height<=480][vcodec^=avc1]+ba[ext=m4a]/"
                  "bv*[height<=480][ext=mp4]+ba[ext=m4a]/"
                  "b[height<=480][ext=mp4]/bv*[height<=480]+ba/b")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if getattr(sys, "frozen", False):
    _BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

WEB_DIR = os.path.join(_BASE_DIR, "web")
CACHE_DIR = os.path.join(WEB_DIR, "_cache")


# --------------------------------------------------------------------------- #
#  Hilfsfunktionen
# --------------------------------------------------------------------------- #
def default_download_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "eden-tools")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def js_runtimes():
    """YouTube verlangt eine JavaScript-Laufzeit (Deno). Die EXE bringt
    deno.exe unter bin/ mit; ohne gebuendelte Datei sucht yt-dlp im PATH."""
    deno = os.path.join(_BASE_DIR, "bin", "deno.exe")
    if os.path.isfile(deno):
        return {"deno": {"path": deno}}
    return {"deno": {}}


class _ErrorLogger:
    """Merkt sich die letzte yt-dlp-Fehlermeldung. Mit ignoreerrors wirft
    yt-dlp keine Exception, sonst ginge der Grund verloren."""

    def __init__(self):
        self.last_error = None

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.last_error = re.sub(r"^ERROR:\s*", "", str(msg))


def _safe_load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _safe_save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _fmt_when(ts):
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if dt.date() == today:
        return "Heute, " + dt.strftime("%H:%M")
    if (today - dt.date()).days == 1:
        return "Gestern, " + dt.strftime("%H:%M")
    return dt.strftime("%d.%m.%Y")


class _CancelDownload(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Backend-Api (vom JS aufrufbar)
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self._window = None
        self._maxed = False
        self._lock = threading.Lock()
        self._cancel = {}
        self._next = 1

        self.cfg_dir = config_dir()
        self.settings = _safe_load(
            os.path.join(self.cfg_dir, "settings.json"),
            {"folder": default_download_dir(), "openAfter": True, "auto": True})
        if not self.settings.get("folder"):
            self.settings["folder"] = default_download_dir()
        self.history = _safe_load(os.path.join(self.cfg_dir, "history.json"), [])
        self.queue = []  # Liste von dicts
        self.upd = aktualisierung.Aktualisierer(APP_VERSION)

        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except Exception:
            pass
        self._clean_cache()

    def set_window(self, window):
        self._window = window

    # ----------------------------------------------------- Fenster -------- #
    def win_minimize(self):
        try:
            self._window.minimize()
        except Exception:
            pass
        return {"ok": True}

    def win_maximize(self):
        try:
            if self._maxed:
                self._window.restore()
            else:
                self._window.maximize()
            self._maxed = not self._maxed
        except Exception:
            try:
                self._window.toggle_fullscreen()
            except Exception:
                pass
        return {"ok": True}

    def win_close(self):
        try:
            self._window.destroy()
        except Exception:
            pass
        return {"ok": True}

    # ----------------------------------------------------- Updates -------- #
    def update_status(self):
        return self.upd.status()

    def update_check(self):
        self.upd.pruefen(pakete=bool(self.settings.get("auto", True)))
        return {"ok": True}

    def update_install(self):
        return {"ok": self.upd.installieren(danach=self.win_close)}

    def open_url(self, url):
        try:
            import webbrowser
            if str(url).startswith("https://"):
                webbrowser.open(url)
        except Exception:
            pass
        return {"ok": True}

    # ----------------------------------------------------- Persistenz ----- #
    def _save_settings(self):
        _safe_save(os.path.join(self.cfg_dir, "settings.json"), self.settings)

    def _save_history(self):
        _safe_save(os.path.join(self.cfg_dir, "history.json"), self.history[:200])

    def _clean_cache(self):
        try:
            for f in os.listdir(CACHE_DIR):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                except Exception:
                    pass
        except Exception:
            pass

    # ----------------------------------------------------- State ---------- #
    def get_state(self):
        return {
            "folder": self.settings.get("folder"),
            "settings": self.settings,
            "queue": self.queue,
            "history": self.history,
        }

    def set_settings(self, key, value):
        self.settings[key] = value
        self._save_settings()
        return {"ok": True}

    # ----------------------------------------------------- Erkennung ------ #
    def detect(self, url):
        url = (url or "").strip()
        if not url:
            return {"ok": False, "error": "leer"}
        is_pl = ("list=" in url) or ("/playlist" in url)
        try:
            import yt_dlp
            opts = {
                "quiet": True, "no_warnings": True, "skip_download": True,
                "noplaylist": not is_pl, "extract_flat": "in_playlist" if is_pl else False,
                "js_runtimes": js_runtimes(),
            }
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=False)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if is_pl or info.get("_type") == "playlist" or info.get("entries") is not None:
            entries = info.get("entries") or []
            return {
                "ok": True, "isPlaylist": True,
                "count": len(entries),
                "title": info.get("title") or "Playlist",
                "channel": info.get("uploader") or info.get("channel") or "",
                "duration": 0,
            }
        return {
            "ok": True, "isPlaylist": False, "count": 1,
            "title": info.get("title") or "(ohne Titel)",
            "channel": info.get("uploader") or info.get("channel") or "",
            "duration": int(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail") or "",
        }

    # ----------------------------------------------------- Vorschau ------- #
    def get_preview(self, url):
        url = (url or "").strip()
        try:
            import yt_dlp
            self._clean_cache()
            name = "preview_%d" % int(time.time() * 1000)
            opts = {
                "quiet": True, "no_warnings": True, "noprogress": True,
                "noplaylist": True,
                "format": PREVIEW_FORMAT, "merge_output_format": "mp4",
                "outtmpl": os.path.join(CACHE_DIR, name + ".%(ext)s"),
                "js_runtimes": js_runtimes(),
            }
            ff = find_ffmpeg()
            if ff:
                opts["ffmpeg_location"] = ff
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=True)
            if info.get("entries"):
                entries = info.get("entries") or []
                info = entries[0] if entries else info
            path = None
            rd = info.get("requested_downloads")
            if rd:
                path = rd[0].get("filepath") or rd[0].get("_filename")
            if not path or not os.path.exists(path):
                files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)]
                files = [f for f in files if os.path.isfile(f)]
                path = files[0] if files else None
            if not path:
                return {"ok": False, "error": "Vorschau-Datei nicht gefunden"}
            return {"ok": True, "src": "_cache/" + os.path.basename(path),
                    "duration": float(info.get("duration") or 0)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----------------------------------------------------- Ordnerwahl ----- #
    def choose_folder(self):
        folder = self.settings.get("folder") or default_download_dir()
        try:
            res = self._window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=folder)
        except Exception:
            res = None
        if res:
            chosen = res[0] if isinstance(res, (list, tuple)) else res
            self.settings["folder"] = chosen
            self._save_settings()
            return {"folder": chosen}
        return {"folder": folder}

    # ----------------------------------------------------- Download ------- #
    def start_download(self, params):
        with self._lock:
            qid = "q%d" % self._next
            self._next += 1
        fmt = params.get("format", "video")
        quality = params.get("quality", "best")
        is_pl = bool(params.get("isPlaylist"))
        mode = params.get("playlistMode")
        is_all = is_pl and mode == "all"
        trim = params.get("trim") or {"on": False}

        segments = self._segments_from(params)
        clip_mode = params.get("clipMode") or "separate"

        fmt_txt = "MP4" if fmt == "video" else "MP3"
        q_txt = ("Beste" if fmt == "audio"
                 else {"best": "1080p+", "good": "720p", "small": "480p"}.get(quality, "Beste"))
        extra = ""
        if segments and not is_all:
            n = len(segments)
            if clip_mode == "remove":
                extra = " · Bereiche entfernt (%d)" % n
            else:
                extra = " · %d Clip%s" % (n, "s" if n != 1 else "")
        sub = fmt_txt + " · " + q_txt + extra
        title = ("Playlist · %d Videos" % params.get("count", 0)) if is_all else (params.get("title") or "Download")

        import random
        item = {
            "id": qid, "title": title, "sub": sub,
            "progress": 0, "status": "queued",
            "hue": random.randint(0, 359), "filepath": None,
        }
        self.queue.insert(0, item)
        self._cancel[qid] = False

        t = threading.Thread(target=self._run, args=(qid, params), daemon=True)
        t.start()
        return {"id": qid}

    def _q(self, qid):
        for it in self.queue:
            if it["id"] == qid:
                return it
        return None

    def _format_opts(self, fmt, quality):
        if fmt == "audio":
            return {
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            }
        if quality == "good":
            h = 720
        elif quality == "small":
            h = 480
        else:
            h = None
        if h:
            f = (f"bv*[height<=?{h}][ext=mp4]+ba[ext=m4a]/"
                 f"bv*[height<=?{h}]+ba/b[height<=?{h}]/b")
        else:
            f = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
        return {"format": f, "merge_output_format": "mp4"}

    def _run(self, qid, params):
        item = self._q(qid)
        if item:
            item["status"] = "downloading"
        try:
            import yt_dlp
            url = params.get("url")
            fmt = params.get("format", "video")
            quality = params.get("quality", "best")
            is_pl = bool(params.get("isPlaylist"))
            mode = params.get("playlistMode")
            is_all = is_pl and mode == "all"
            trim = params.get("trim") or {"on": False}
            folder = self.settings.get("folder") or default_download_dir()
            os.makedirs(folder, exist_ok=True)

            log = _ErrorLogger()
            opts = {
                "quiet": True, "no_warnings": True, "ignoreerrors": True,
                "noprogress": True, "retries": 5, "fragment_retries": 5,
                "noplaylist": not is_all,
                "progress_hooks": [self._make_hook(qid, is_all)],
                "logger": log,
                "js_runtimes": js_runtimes(),
            }
            ff = find_ffmpeg()
            if ff:
                opts["ffmpeg_location"] = ff
            opts.update(self._format_opts(fmt, quality))

            segments = self._segments_from(params)
            clip_mode = params.get("clipMode") or "separate"
            do_segments = bool(segments) and not is_all

            if do_segments:
                # Ganzes Video in einen Temp-Ordner laden, dann mit ffmpeg
                # schneiden: Einzel-Clips ODER markierte Bereiche entfernen.
                workdir = tempfile.mkdtemp(prefix="edenclip_")
                opts["outtmpl"] = os.path.join(workdir, "full.%(ext)s")
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(url, download=True)
                full = self._final_filepath(info)
                title = (info.get("title") if info else None) or "video"
                duration = float((info.get("duration") if info else 0) or 0)
                it = self._q(qid)
                if it:
                    it["progress"] = 95
                    it["status"] = "downloading"
                outputs = []
                if full and os.path.exists(full):
                    outputs = self._cut_segments(
                        full, segments, clip_mode, fmt, title, folder, duration)
                shutil.rmtree(workdir, ignore_errors=True)
                if not full:
                    raise RuntimeError(log.last_error or "Download fehlgeschlagen.")
                if not outputs:
                    raise RuntimeError("Schnitt fehlgeschlagen.")
                filepath = outputs[0]
            else:
                if is_all:
                    opts["outtmpl"] = os.path.join(
                        folder, "%(playlist_title)s",
                        "%(playlist_index)03d - %(title)s.%(ext)s")
                else:
                    opts["outtmpl"] = os.path.join(folder, "%(title)s.%(ext)s")
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(url, download=True)
                filepath = self._final_filepath(info)
                # Einzelvideo ohne Datei = gescheitert; bei Playlists darf
                # ein einzelnes Video fehlen, aber nicht alle.
                if not info or (not is_all and not filepath):
                    raise RuntimeError(log.last_error or "Download fehlgeschlagen.")

            it = self._q(qid)
            if it:
                it["progress"] = 100
                it["status"] = "done"
                it["filepath"] = filepath or folder

            # Verlauf
            self.history.insert(0, {
                "id": "h" + qid,
                "title": it["title"] if it else (params.get("title") or "Download"),
                "sub": it["sub"] if it else "",
                "when": _fmt_when(time.time()),
                "hue": it["hue"] if it else 200,
                "filepath": filepath or folder,
            })
            self._save_history()

            if self.settings.get("openAfter"):
                self._reveal(filepath or folder)

        except _CancelDownload:
            self.queue[:] = [x for x in self.queue if x["id"] != qid]
        except Exception as e:
            it = self._q(qid)
            if it:
                it["status"] = "error"
                it["sub"] = (it.get("sub") or "") + " — " + str(e)[:80]
        finally:
            if self._cancel.get(qid):
                self.queue[:] = [x for x in self.queue if x["id"] != qid]
            self._cancel.pop(qid, None)

    def _make_hook(self, qid, is_all):
        def hook(d):
            if self._cancel.get(qid):
                raise _CancelDownload()
            it = self._q(qid)
            if not it:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                frac = (done / total) if total else 0
                if is_all:
                    info = d.get("info_dict", {})
                    idx = info.get("playlist_index") or 1
                    n = info.get("n_entries") or 1
                    it["progress"] = min(99, ((idx - 1 + frac) / max(n, 1)) * 100)
                    it["sub"] = (it["sub"].split(" — Video")[0]) + (" — Video %d/%d" % (idx, n))
                else:
                    it["progress"] = min(99, frac * 100)
            elif status == "finished":
                it["progress"] = max(it.get("progress", 0), 99)
        return hook

    @staticmethod
    def _final_filepath(info):
        if not info:
            return None
        if info.get("entries"):
            entries = info.get("entries") or []
            info = entries[0] if entries else info
        rd = info.get("requested_downloads")
        if rd:
            return rd[0].get("filepath") or rd[0].get("_filename")
        return info.get("filepath") or info.get("_filename")

    # ----------------------------------------------------- Segmente ------- #
    @staticmethod
    def _segments_from(params):
        """Liefert die Liste der Bereiche [{start,end}] aus den Parametern.
        Faellt auf die einzelne trim-Auswahl zurueck, falls keine Segmente
        explizit gesetzt wurden."""
        segs = []
        for s in (params.get("segments") or []):
            try:
                a = float(s.get("start")); b = float(s.get("end"))
            except Exception:
                continue
            if b > a:
                segs.append({"start": a, "end": b})
        if not segs:
            trim = params.get("trim") or {}
            if trim.get("on"):
                try:
                    a = float(trim.get("start") or 0)
                    b = float(trim.get("end") or 0)
                    if b > a:
                        segs.append({"start": a, "end": b})
                except Exception:
                    pass
        return segs

    @staticmethod
    def _sanitize(name):
        name = re.sub(r'[\\/:*?"<>|]', "", str(name or "video")).strip()
        return (name or "video")[:120]

    @staticmethod
    def _unique(path):
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 2
        while os.path.exists("%s (%d)%s" % (base, i, ext)):
            i += 1
        return "%s (%d)%s" % (base, i, ext)

    def _run_ffmpeg(self, cmd):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    creationflags=_NO_WINDOW)
            proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    def _cut_segments(self, full, segments, mode, fmt, title, outdir, duration):
        ff = find_ffmpeg()
        if not ff:
            return []
        safe = self._sanitize(title)
        ext = ".mp3" if fmt == "audio" else ".mp4"
        segs = sorted((s["start"], s["end"]) for s in segments)
        outputs = []

        if mode == "remove":
            # Komplement bilden: alles AUSSER den markierten Bereichen behalten
            kept, cur = [], 0.0
            for s, e in segs:
                if s > cur:
                    kept.append((cur, s))
                cur = max(cur, e)
            if duration and duration > cur + 0.05:
                kept.append((cur, duration))
            if not kept:
                return []
            out = self._unique(os.path.join(
                outdir, "%s [ohne Markierungen]%s" % (safe, ext)))
            if self._concat_segments(ff, full, kept, fmt, out):
                outputs.append(out)
        else:
            for i, (s, e) in enumerate(segs, 1):
                out = self._unique(os.path.join(
                    outdir, "%s [Clip %d %d-%ds]%s" % (safe, i, int(s), int(e), ext)))
                if self._cut_one(ff, full, s, e, fmt, out):
                    outputs.append(out)
        return outputs

    def _cut_one(self, ff, full, s, e, fmt, out):
        if fmt == "audio":
            cmd = [ff, "-y", "-ss", str(s), "-to", str(e), "-i", full,
                   "-vn", "-c:a", "libmp3lame", "-q:a", "2", out]
        else:
            cmd = [ff, "-y", "-ss", str(s), "-to", str(e), "-i", full,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-c:a", "aac", "-b:a", "192k", out]
        if self._run_ffmpeg(cmd):
            return True
        return self._run_ffmpeg([ff, "-y", "-ss", str(s), "-to", str(e),
                                 "-i", full, "-c", "copy", out])

    def _concat_segments(self, ff, full, kept, fmt, out):
        if fmt == "audio":
            filt = "".join(
                "[0:a]atrim=start=%s:end=%s,asetpts=PTS-STARTPTS[a%d];" % (s, e, i)
                for i, (s, e) in enumerate(kept))
            filt += "".join("[a%d]" % i for i in range(len(kept)))
            filt += "concat=n=%d:v=0:a=1[outa]" % len(kept)
            cmd = [ff, "-y", "-i", full, "-filter_complex", filt,
                   "-map", "[outa]", "-c:a", "libmp3lame", "-q:a", "2", out]
        else:
            filt = ""
            for i, (s, e) in enumerate(kept):
                filt += "[0:v]trim=start=%s:end=%s,setpts=PTS-STARTPTS[v%d];" % (s, e, i)
                filt += "[0:a]atrim=start=%s:end=%s,asetpts=PTS-STARTPTS[a%d];" % (s, e, i)
            filt += "".join("[v%d][a%d]" % (i, i) for i in range(len(kept)))
            filt += "concat=n=%d:v=1:a=1[outv][outa]" % len(kept)
            cmd = [ff, "-y", "-i", full, "-filter_complex", filt,
                   "-map", "[outv]", "-map", "[outa]",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-c:a", "aac", "-b:a", "192k", out]
        return self._run_ffmpeg(cmd)

    def cancel(self, qid):
        self._cancel[qid] = True
        it = self._q(qid)
        if it and it.get("status") in ("done", "error", "queued"):
            self.queue[:] = [x for x in self.queue if x["id"] != qid]
        return {"ok": True}

    # ----------------------------------------------------- Ordner zeigen -- #
    def open_folder(self, path):
        try:
            if path and os.path.isfile(path):
                if sys.platform.startswith("win"):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                else:
                    self._open_dir(os.path.dirname(path))
            else:
                folder = path if (path and os.path.isdir(path)) else self.settings.get("folder")
                self._open_dir(folder)
        except Exception:
            pass
        return {"ok": True}

    @staticmethod
    def _open_dir(folder):
        if not folder:
            return
        if sys.platform.startswith("win"):
            os.startfile(folder)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _reveal(self, path):
        try:
            self.open_folder(path)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Start
# --------------------------------------------------------------------------- #
def _testmodus(argv):
    """Ohne Fenster pruefen/aktualisieren und das Ergebnis als JSON ablegen:
       --pruefen <datei>       yt-dlp nachziehen + App-Release nachsehen
       --aktualisieren <datei> dazu die EXE wirklich tauschen und neu starten"""
    modus = argv[1]
    ziel = argv[2] if len(argv) > 2 else os.path.join(tempfile.gettempdir(), "eden_update_test.json")
    out = {"app": APP_VERSION, "exe": sys.executable}
    try:
        out["ytdlp_update"] = aktualisierung.ytdlp_aktualisieren()
        import yt_dlp
        from yt_dlp import version as _v
        out["ytdlp_geladen"] = {"version": _v.__version__, "datei": yt_dlp.__file__}
        try:
            import yt_dlp_ejs
            out["ejs_datei"] = yt_dlp_ejs.__file__
        except Exception as e:
            out["ejs_datei"] = "fehlt: %s" % e
        info = aktualisierung.neueste_app()
        out["release"] = info
        out["neuer"] = bool(info and aktualisierung.ver_tuple(info["version"]) > aktualisierung.ver_tuple(APP_VERSION))
        if modus == "--aktualisieren" and out["neuer"]:
            aktualisierung.app_tauschen(info)
            out["getauscht"] = True
    except Exception as e:
        out["fehler"] = repr(e)
    _safe_save(ziel, out)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--pruefen", "--aktualisieren"):
        return _testmodus(sys.argv)
    api = Api()
    window = webview.create_window(
        APP_NAME,
        os.path.join(WEB_DIR, "index.html"),
        js_api=api,
        width=940, height=760, min_size=(900, 700),
        background_color="#e9ebee",
        frameless=True,        # eigene Titelleiste statt Windows-Rahmen
        easy_drag=False,       # nur die markierte Drag-Region zieht das Fenster
    )
    api.set_window(window)
    webview.start(http_server=True)


if __name__ == "__main__":
    main()
