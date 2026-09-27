# -*- coding: utf-8 -*-
"""
Selbst-Aktualisierung des Eden.tools Downloaders.

Zwei getrennte Wege:

1. App-Update  - die neueste Fassung steht als GitHub-Release in marvEDEN/eden-downloader
   (Datei Eden-Downloader.exe). Die laufende EXE laedt sie neben sich, prueft die SHA-256,
   benennt sich selbst in "<exe>.alt" um (das erlaubt Windows auch fuer eine laufende EXE),
   legt die neue an ihren Platz und startet sie. Die ".alt" raeumt der naechste Start weg.

2. yt-dlp      - bricht bei YouTube-Aenderungen am haeufigsten. Die App holt sich die
   neueste Fassung (und das dazu passende yt-dlp-ejs) selbst von PyPI und legt sie unter
   %LOCALAPPDATA%\\eden-tools\\pakete\\<paket>\\<version> ab. Diese Ordner kommen VOR die
   gebuendelte Fassung in sys.path. Dafuer braucht es kein neues Release.

Zum Testen lassen sich die Quellen umbiegen:
  EDEN_UPDATE_API  - Adresse statt der GitHub-API (liefert dasselbe JSON wie releases/latest)
  EDEN_PYPI        - Basisadresse statt https://pypi.org/pypi
"""

import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import tempfile
import threading
import subprocess
import urllib.request

REPO = "marvEDEN/eden-downloader"
ASSET = "Eden-Downloader.exe"
API_URL = os.environ.get("EDEN_UPDATE_API") or ("https://api.github.com/repos/%s/releases/latest" % REPO)
PYPI = (os.environ.get("EDEN_PYPI") or "https://pypi.org/pypi").rstrip("/")
UA = "EdenDownloader-Updater"


# --------------------------------------------------------------------------- #
#  Hilfen
# --------------------------------------------------------------------------- #
def ver_tuple(v):
    """'2026.09.20' / 'v1.2.0' / '1.2.0.post1' -> vergleichbares Tupel."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url):
    with _get(url) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url, ziel, sha256=None, fortschritt=None):
    """Laedt url nach ziel (erst nach ziel.teil, dann umbenennen) und prueft die Pruefsumme."""
    teil = ziel + ".teil"
    h = hashlib.sha256()
    with _get(url, timeout=60) as r, open(teil, "wb") as f:
        gesamt = int(r.headers.get("Content-Length") or 0)
        geladen = 0
        while True:
            block = r.read(1 << 16)
            if not block:
                break
            f.write(block)
            h.update(block)
            geladen += len(block)
            if fortschritt and gesamt:
                fortschritt(geladen / gesamt)
    if sha256 and h.hexdigest().lower() != sha256.lower():
        try:
            os.remove(teil)
        except OSError:
            pass
        raise RuntimeError("Pruefsumme stimmt nicht")
    os.replace(teil, ziel)


def daten_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "eden-tools")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
#  yt-dlp (+ yt-dlp-ejs) von PyPI
# --------------------------------------------------------------------------- #
PAKETE_DIR = os.path.join(daten_dir(), "pakete")


def _installiert(paket):
    """Neueste vollstaendig entpackte Fassung eines Pakets: (version, ordner) oder (None, None)."""
    d = os.path.join(PAKETE_DIR, paket)
    try:
        vs = [v for v in os.listdir(d) if os.path.isfile(os.path.join(d, v, ".fertig"))]
    except OSError:
        return None, None
    if not vs:
        return None, None
    v = max(vs, key=ver_tuple)
    return v, os.path.join(d, v)


def pakete_einhaengen():
    """Beim Start, BEVOR yt_dlp importiert wird: geholte Fassungen vor die gebuendelten setzen."""
    for paket in ("yt-dlp-ejs", "yt-dlp"):
        v, ordner = _installiert(paket)
        if ordner and ordner not in sys.path:
            sys.path.insert(0, ordner)


def gebuendelt(paket):
    """Die Fassung, die ohne unsere Ordner geladen wuerde (Metadaten der EXE bzw. von pip)."""
    try:
        from importlib import metadata
        for dist in metadata.distributions():
            name = (dist.metadata.get("Name") or "").lower().replace("_", "-")
            if name != paket:
                continue
            pfad = str(getattr(dist, "_path", ""))
            if PAKETE_DIR.lower() in pfad.lower():
                continue
            return dist.version
    except Exception:
        pass
    return None


def wirksam(paket):
    v, _ = _installiert(paket)
    g = gebuendelt(paket)
    if v and (not g or ver_tuple(v) >= ver_tuple(g)):
        return v
    return g


def _paket_holen(paket, version=None):
    """Holt paket (in version oder die neueste) als Wheel, wenn neuer als das Wirksame. -> neue Version oder None."""
    info = _get_json("%s/%s/%sjson" % (PYPI, paket, (version + "/") if version else ""))
    neu = info["info"]["version"]
    if ver_tuple(neu) <= ver_tuple(wirksam(paket)):
        return None, info
    wheel = next((u for u in info.get("urls", [])
                  if u.get("packagetype") == "bdist_wheel" and u.get("filename", "").endswith("-none-any.whl")), None)
    if not wheel:
        return None, info
    ziel_dir = os.path.join(PAKETE_DIR, paket, neu)
    tmp = tempfile.mkdtemp(prefix="eden_pkg_")
    try:
        whl = os.path.join(tmp, wheel["filename"])
        _download(wheel["url"], whl, (wheel.get("digests") or {}).get("sha256"))
        ent = os.path.join(tmp, "x")
        with zipfile.ZipFile(whl) as z:
            z.extractall(ent)
        open(os.path.join(ent, ".fertig"), "w").close()
        shutil.rmtree(ziel_dir, ignore_errors=True)
        os.makedirs(os.path.dirname(ziel_dir), exist_ok=True)
        shutil.move(ent, ziel_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _aufraeumen(paket, behalten=neu)
    return neu, info


def _aufraeumen(paket, behalten):
    """Alte Fassungen loeschen; eine gerade geladene (in sys.path) bleibt bis zum naechsten Start."""
    d = os.path.join(PAKETE_DIR, paket)
    try:
        for v in os.listdir(d):
            ordner = os.path.join(d, v)
            if v != behalten and ordner not in sys.path:
                shutil.rmtree(ordner, ignore_errors=True)
    except OSError:
        pass


def ytdlp_aktualisieren():
    """-> {'ytdlp': version, 'neu': bool, 'sofort': bool}. Wirft bei Netzfehlern."""
    neu, info = _paket_holen("yt-dlp")
    # yt-dlp nennt das passende yt-dlp-ejs in seinen Abhaengigkeiten ("yt-dlp-ejs==0.3.0; extra == 'default'")
    ejs = None
    for req in (info.get("info", {}).get("requires_dist") or []):
        m = re.match(r"\s*yt-dlp-ejs\s*==\s*([\w.]+)", req)
        if m:
            ejs = m.group(1)
    if ejs:
        try:
            _paket_holen("yt-dlp-ejs", ejs)
        except Exception:
            pass
    sofort = False
    if neu and "yt_dlp" not in sys.modules:
        # Noch nichts geladen -> gilt schon fuer diese Sitzung
        pakete_einhaengen()
        sofort = True
    return {"ytdlp": wirksam("yt-dlp"), "neu": bool(neu), "sofort": sofort}


# --------------------------------------------------------------------------- #
#  App-Update ueber GitHub-Releases
# --------------------------------------------------------------------------- #
def neueste_app():
    data = _get_json(API_URL)
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    if not asset:
        return None
    sha = (asset.get("digest") or "")
    sha = sha.split(":", 1)[1] if sha.startswith("sha256:") else ""
    if not sha:
        # Fallback: das Release-Skript legt immer auch <ASSET>.sha256 dazu
        s = next((a for a in data.get("assets", []) if a.get("name") == ASSET + ".sha256"), None)
        if s:
            with _get(s["browser_download_url"]) as r:
                sha = r.read().decode("utf-8", "replace").split()[0]
    return {
        "version": str(data.get("tag_name", "")).lstrip("vV"),
        "notes": data.get("body") or "",
        "url": asset["browser_download_url"],
        "size": asset.get("size") or 0,
        "sha256": sha,
        "seite": data.get("html_url") or ("https://github.com/%s/releases/latest" % REPO),
    }


def kann_sich_tauschen():
    return bool(getattr(sys, "frozen", False)) and sys.executable.lower().endswith(".exe")


def reste_wegraeumen():
    """Die beim letzten Update umbenannte alte EXE loeschen (laeuft dann nicht mehr)."""
    if not kann_sich_tauschen():
        return
    for endung in (".alt", ".neu", ".neu.teil"):
        p = sys.executable + endung
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def app_tauschen(info, fortschritt=None):
    """Laedt die neue EXE, tauscht sie gegen die laufende und startet sie. Danach muss das Fenster zu."""
    if not kann_sich_tauschen():
        raise RuntimeError("Nur die EXE kann sich selbst aktualisieren")
    if not info.get("sha256"):
        raise RuntimeError("Release ohne Pruefsumme")
    exe = sys.executable
    neu = exe + ".neu"
    alt = exe + ".alt"
    _download(info["url"], neu, info["sha256"], fortschritt)
    if os.path.exists(alt):
        os.remove(alt)
    os.replace(exe, alt)
    try:
        os.replace(neu, exe)
    except OSError:
        os.replace(alt, exe)  # zurueck, sonst stuende gar keine EXE mehr da
        raise
    env = dict(os.environ)
    # Sonst erbt die neue onefile-EXE den Entpack-Ordner der alten (der beim Beenden verschwindet)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in list(env):
        if k.startswith("_PYI") or k.startswith("_MEI"):
            env.pop(k, None)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe], env=env, close_fds=True, creationflags=flags, cwd=os.path.dirname(exe))


# --------------------------------------------------------------------------- #
#  Zustand fuer die Oberflaeche
# --------------------------------------------------------------------------- #
class Aktualisierer:
    def __init__(self, app_version):
        self.app_version = app_version
        self._lock = threading.Lock()
        self.st = {
            "app": app_version,
            "ytdlp": None,
            "ytdlpNeu": False,       # neue Fassung geladen, gilt erst nach Neustart
            "neu": None,             # {"version","notes","seite"} wenn es eine neuere App gibt
            "phase": "idle",         # idle | pruefen | laden | neustart | fehler
            "fortschritt": 0,
            "fehler": "",
            "tauschbar": kann_sich_tauschen(),
        }

    def status(self):
        with self._lock:
            if self.st["ytdlp"] is None:
                self.st["ytdlp"] = wirksam("yt-dlp")
            return dict(self.st)

    def _set(self, **kw):
        with self._lock:
            self.st.update(kw)

    def pruefen(self, pakete=True):
        """Im Hintergrund: yt-dlp nachziehen und nach einer neuen App-Fassung sehen."""
        with self._lock:
            if self.st["phase"] in ("pruefen", "laden", "neustart"):
                return
            self.st.update(phase="pruefen", fehler="")

        def lauf():
            if pakete:
                try:
                    r = ytdlp_aktualisieren()
                    self._set(ytdlp=r["ytdlp"], ytdlpNeu=(r["neu"] and not r["sofort"]))
                except Exception:
                    pass
            try:
                info = neueste_app()
                if info and ver_tuple(info["version"]) > ver_tuple(self.app_version):
                    self._set(neu=info)
                else:
                    self._set(neu=None)
                self._set(phase="idle")
            except Exception as e:
                self._set(phase="idle", fehler="Update-Pruefung: %s" % e)
        threading.Thread(target=lauf, daemon=True).start()

    def installieren(self, danach=None):
        info = self.st.get("neu")
        if not info or self.st["phase"] == "laden":
            return False

        def lauf():
            self._set(phase="laden", fortschritt=0, fehler="")
            try:
                app_tauschen(info, lambda f: self._set(fortschritt=round(f * 100)))
                self._set(phase="neustart", fortschritt=100)
                if danach:
                    danach()
            except Exception as e:
                self._set(phase="fehler", fehler=str(e))
        threading.Thread(target=lauf, daemon=True).start()
        return True
