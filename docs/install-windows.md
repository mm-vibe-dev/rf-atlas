# Windows 11 installation

Install Python 3.11+ separately, then double-click `install-windows.cmd`. It creates a project virtual environment, installs application dependencies, asks for the existing RTL tools folder when configuration is missing, and opens the app. Existing private configuration is preserved. Later, double-click `rf-atlas.cmd` to launch. No administrator access or driver replacement is performed.

For a manual installation, run from the project directory in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[plots]"
Copy-Item config.example.toml config.local.toml
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\rf-atlas.exe --config config.local.toml --help
```

Set each [tools] entry to the existing compatible RTL executable path, or add its directory to PATH for this shell. Use TOML single quotes for Windows backslash paths. Do not replace the working RTL-SDR Blog V4L DLL, drivers or Zadig configuration. The configured executable determines which adjacent DLL/runtime it loads; RF Atlas does not load or replace a DLL itself.

Use a local device index or privately configured serial. The default public example uses index 0. runtime.root resolves relative to the TOML file and should point to a private directory. With no file, the default is the user's LOCALAPPDATA RF-Atlas directory. RF_ATLAS_CONFIG selects a file; RF_ATLAS_HOME overrides its root. config.local.toml in the working directory is an explicit local convenience fallback.

Doctor opens the receiver briefly. Close other receiving applications before doctor, survey or record. No admin rights are required when the existing receiver permissions/driver setup works. Missing executable/DLL/device errors are surfaced; no automatic installation or driver repair occurs.

```powershell
.\.venv\Scripts\rf-atlas.exe --config config.local.toml doctor
.\.venv\Scripts\rf-atlas.exe --config config.local.toml survey --start 100M --stop 100.2M --profile deep --gain 15.7 --integration 1 --duration 10
```

The example band is illustrative, not a signal label or a claim of antenna sensitivity. Defaults remain receive-only and bias tee disabled. The legacy rf-atlas.cmd uses the local virtual environment when present, otherwise Python on PATH, and loads the adjacent private config when available.

## First launch of the radio application

After configuring the existing RTL tools, start the unified backend and interface:

```powershell
.\.venv\Scripts\rf-atlas.exe --config config.local.toml app
```

The application prints its localhost address. Open that address in a local
browser if a browser does not open automatically. Keep this process running
while using the interface; Ctrl+C stops acquisition and closes the server.
The server binds to loopback, and does not require a separate frontend service.
Add `--open-browser` to request that the default browser open on launch.

A fresh installation starts idle. In Tuner, enter a supported frequency and
manual gain, then start the receiver. Spectrum measurements and waterfall rows
update after each bounded integration. This view measures relative receiver
power; it does not provide an audio stream. Stop the tuner before unplugging the
receiver. A failed acquisition reports an error and leaves retained observations
available in Signals and History.

For unattended discovery, select Quick, Full or Smart in Scanner and enable
Auto Survey. Progress and investigation jobs persist locally. Full is a sequence
of frequency sections, and one visit does not establish an empty or completely
characterized band. Editable private profiles live under the runtime directory;
capture permissions are explicit per profile. Stopping Auto Survey releases the
receiver. Manual tuning pauses automatic acquisition; the restore preference
controls whether surveying resumes afterward.

Settings stores the startup preference and independent identifier/frame
retention choices. Both retention choices default off. Enable identifier
retention to populate Devices and Stations from supported verified decoder
fields. Raw frame bytes may themselves contain identifiers and message content,
even when parsed identifier retention is off. A missing raw frame is shown as
withheld or unavailable, rather than fabricated from IQ samples. Changing these
preferences affects future results; it does not erase already retained data.
