# Eden.tools — Downloader

Moderner Video-Downloader (YouTube, Vimeo, TikTok …) mit freundlichem Web-UI in
einem nativen Fenster. Backend: **yt-dlp + ffmpeg** (aus dem Vorgaenger-Tool
uebernommen), Oberflaeche: **pywebview** (HTML/CSS/JS), umgesetzt nach dem
eden.tools-Design-Handoff.

## Features

- Vier Ansichten: **Neuer Download**, **Warteschlange**, **Verlauf**, **Einstellungen**
- Link einfuegen → **Auto-Erkennung** (Titel, Kanal, Dauer) inkl. **Playlist-Dialog**
- Format **Video / Nur Musik**, Qualitaet **Beste / 720p / 480p**
- **Eingebettete Vorschau mit Ton** (HTML5-Player) + **Zuschnitt** mit ziehbaren
  Start/Ende-Griffen am Scrubber
- **Warteschlange** mit Live-Fortschritt, **Verlauf** mit „Ordner oeffnen"
- Abhaengigkeiten & Protokoll laufen unsichtbar im Hintergrund

## Voraussetzung: Python-Version

Wegen `pywebview` (bzw. dessen Windows-Komponente) bitte **Python 3.11, 3.12
oder 3.13** verwenden — **nicht 3.14** (dafuer fehlen noch fertige Pakete).

```
winget install Python.Python.3.13
```

## Schnellstart

1. **`Setup (einmalig).bat`** doppelklicken → installiert pywebview, yt-dlp, ffmpeg.
2. **`Eden Downloader starten.bat`** doppelklicken → App startet.

Oder manuell:

```
py -3.13 -m pip install pywebview yt-dlp imageio-ffmpeg
py -3.13 eden_downloader.py
```

## Eigenstaendige EXE und Releases

Nur Python 3.12 ist auf dem Rechner, die Skripte benutzen es direkt.

**Patch veroeffentlichen** (in PowerShell, in diesem Ordner):

1. In `eden_downloader.py` die Zeile `APP_VERSION = "…"` hochzaehlen.
2. In `CHANGELOG.md` einen Abschnitt `## <version>` schreiben. Das werden die Release-Notizen.
3. `.\veroeffentlichen.ps1` → baut `dist\Eden-Downloader.exe` und legt sie als GitHub-Release
   `v<version>` in `marvEDEN/eden-downloader` ab (dazu `Eden-Downloader.exe.sha256`).

Alle installierten Apps sehen das Release beim naechsten Start unten links als „Update".
Ein Klick laedt die EXE, prueft die SHA-256, tauscht die laufende Datei aus und startet neu
(`aktualisierung.py`). Die Website verlinkt immer
`https://github.com/marvEDEN/eden-downloader/releases/latest/download/Eden-Downloader.exe`.

`.\veroeffentlichen.ps1 -NurBauen` baut nur, ohne zu veroeffentlichen.

**yt-dlp braucht kein Release:** Die App holt sich beim Start selbst die neueste Fassung von
PyPI (plus das passende `yt-dlp-ejs`) nach `%LOCALAPPDATA%\eden-tools\pakete\` und zieht sie
der eingebauten vor. Abschaltbar ueber „Werkzeuge automatisch aktuell halten".

**Ohne Fenster pruefen:** `Eden-Downloader.exe --pruefen <datei.json>` bzw.
`--aktualisieren <datei.json>`. Mit `EDEN_UPDATE_API=<url>` laesst sich die Release-Quelle
fuer Tests auf einen lokalen Server umbiegen.

## Projektstruktur

| Pfad | Zweck |
|---|---|
| `eden_downloader.py` | Bootstrap + Backend-Bruecke (`Api`) |
| `web/index.html` | Oberflaeche (Markup) |
| `web/styles.css` | Design (Tokens, Komponenten) |
| `web/app.js` | Frontend-Logik + Aufrufe ans Backend |
| `web/fonts/` | Marken-Fonts (Founders Grotesk X-Condensed, Inter Tight) |
| `Setup (einmalig).bat` | installiert die Abhaengigkeiten |
| `Eden Downloader starten.bat` | startet die App (ohne Konsole) |
| `aktualisierung.py` | Selbst-Update (GitHub-Release) und yt-dlp von PyPI |
| `veroeffentlichen.ps1` | baut die EXE und veroeffentlicht sie als Release |
| `CHANGELOG.md` | Release-Notizen je Version |

Einstellungen & Verlauf werden unter `%APPDATA%\eden-tools\` gespeichert.

## Bekannte offene Punkte / Hinweise

- **Vorschau:** laedt eine kleine 360p-Kopie in `web/_cache/` und spielt sie im
  HTML5-Player ab (mit Ton). Start/Ende beziehen sich auf die Originaldauer.
- **Zuschnitt** nutzt aktuell den schnellen Weg (`download_ranges`, laedt nur den
  Abschnitt). Ein „beste Qualitaet" (ganzes Video, dann schneiden) laesst sich
  spaeter als Option ergaenzen.
- **App-Icon (.ico)** fehlt noch — das eden-Gem liegt nur als CSS vor.

## Rechtlicher Hinweis

Nur Inhalte herunterladen, an denen man die Rechte hat oder deren Lizenz das
erlaubt.
