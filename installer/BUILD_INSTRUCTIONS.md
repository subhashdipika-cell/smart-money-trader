# Building the Smart Money Trader installers

You get **two** setup files, both produced by one script:

| File | Size | What the user needs |
|------|------|---------------------|
| `SmartMoneyTrader_Setup_Online.exe` | small (~10 MB) | Internet during first launch. Downloads Python + packages automatically. |
| `SmartMoneyTrader_Setup_Offline_SelfContained.exe` | large (~120 MB) | Nothing. Python runtime + all packages are bundled. Works with no internet. |

Both install the same app and run it the same way: the Python backend serves the
dashboard at **http://127.0.0.1:8000/app**. **Node.js is NOT required on the user's PC.**

---

## One-time setup on YOUR build PC (not the user's)

Install these once, on the machine where you build the installers:

1. **NSIS** - https://nsis.sourceforge.io  -> during install, keep defaults.
   Make sure `makensis.exe` is on your PATH (usually `C:\Program Files (x86)\NSIS`).
2. **Node.js** - https://nodejs.org  -> used to build the dashboard each time.
3. **Python 3.x** - https://www.python.org/downloads  -> tick *"Add Python to PATH"*.
   (Used to download the offline packages.)
4. **Internet** - needed once, so the script can fetch the runtime + packages for
   the offline installer.

---

## Build (one click)

From the project root (`D:\smart-money-trader`), double-click:

```
build_installers.bat
```

It will:
1. Build the dashboard (`frontend\dist`) with the correct `/app` paths.
2. Build the **online** installer.
3. Download the bundled Python runtime + all packages.
4. Build the **offline** installer.

When it finishes, both `.exe` files are in the **`installer_output\`** folder.

---

## Installing on a new PC

1. Copy the chosen `.exe` to the new PC.
2. Run it (right-click -> *Run as administrator* the first time).
3. On the last wizard screen, leave **"Run first-time setup"** ticked and click Finish.
   - Online: downloads Python + packages (needs internet, ~2-4 min).
   - Offline: unpacks the bundled runtime + packages (no internet, ~2-3 min).
4. Launch from the **Start Menu -> Smart Money Trader -> Start Smart Money Trader**.
   The dashboard opens at http://127.0.0.1:8000/app.

To enter MT5 credentials, edit `backend\mt4_config.json` inside the install folder
(default `C:\Program Files\SmartMoneyTrader`).

---

## Notes & troubleshooting

- **Python version**: the bundled runtime is **3.11.9**, and offline wheels are
  fetched for Python 3.11 (`win_amd64`). If you change `backend\requirements.txt`
  to a package with no 3.11 Windows wheel, the wheel-download step will stop and
  tell you which one.
- **`makensis not found`**: NSIS isn't on PATH. Add `C:\Program Files (x86)\NSIS`
  to your PATH, or call the full path to `makensis.exe`.
- **Rebuilding from scratch**: delete `build_assets\` and `wheels\` to force a
  fresh download of the runtime and packages.
- The dev virtual environment (`backend\.venv`) is intentionally **excluded** from
  both installers - the runtime is created fresh on the user's PC.
- The older `installer\installer.nsi` and `installer\setup_runtime.bat` are the
  previous version and are no longer used; `build_installers.bat` uses the new
  `smt_online.nsi` / `smt_offline.nsi` scripts.
