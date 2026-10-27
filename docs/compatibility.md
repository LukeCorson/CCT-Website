# Desktop compatibility and moving between computers

CCT Control is a local desktop app. It runs independently of Codex, ChatGPT, or an online account after its Python dependencies are installed. Its UI, charts, data files, and serial communication are implemented with portable Python and Qt APIs. This build has been exercised on Windows; macOS and Linux setup paths are provided, but need native-machine acceptance testing before laboratory use.

## Supported installation model

Use Python 3.11 or newer with a compatible PySide6 wheel for the computer's operating system and processor. Qt's supported operating-system versions change across releases; consult the [Qt platform requirements](https://doc.qt.io/qtforpython-6/overviews/qtdoc-supported-platforms.html) and [Qt for Python setup guide](https://doc.qt.io/qtforpython-6/quickstart.html) if pip cannot install a compatible build. The project declares compatible dependency ranges; `requirements-lock.txt` records the exact Windows versions that were tested.

For a native package built for that computer's OS and processor, extract the complete ZIP and open the application while keeping its `_internal` folder alongside it. Python and Codex are not required. This locally tested Windows build is unsigned and has not been accepted on a second physical machine; follow the lab's software-approval process. USB adapter drivers remain separate.

For the source package, copy the folder to another computer, install Python there, and run setup there. Recreate `.venv` on every computer; a virtual environment copied from a different machine or operating system is not portable. Initial setup needs internet access to download packages. Normal local simulation, recording, and instrument control do not need internet access.

| Desktop | One-time setup | Start |
| --- | --- | --- |
| Windows | Double-click `Setup CCT Control.cmd` | Double-click `Start CCT Control.cmd` |
| macOS | In Terminal, run `sh scripts/setup.sh` from the project folder | Run `sh scripts/start.sh` |
| Linux | In a terminal, run `sh scripts/setup.sh` from the project folder | Run `sh scripts/start.sh` |

On macOS, the `.command` files provide optional Finder launchers. A copied checkout may not preserve their executable permissions. From the project folder, enable them once with:

```sh
chmod +x "Setup CCT Control.command" "Start CCT Control.command"
```

If the chosen `python3` is too old, select an installed interpreter explicitly, for example:

```sh
CCT_PYTHON=/path/to/python3.12 sh scripts/setup.sh
```

Linux needs a graphical desktop, Qt's platform libraries, and Python's `venv` support. Install missing OS packages through the distribution's normal package manager when the setup or Qt error identifies them. Serial-port access may also need a lab administrator to grant the current user access to the device. Run the app as the ordinary user who owns the experiment records.

## Where records live

New installations keep records outside the source folder, separately for each operating-system user:

| Desktop | Default data folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\CCTControl` |
| macOS | `~/Library/Application Support/CCTControl` |
| Linux | `$XDG_DATA_HOME/cct-control`, or `~/.local/share/cct-control` when unset |

The data folder contains `runs/`, `profiles/`, `support/`, legacy `recipes/`, and `logs/`. The app's `--data-dir` option takes precedence over `CCT_DATA_DIR`, which takes precedence over the OS default. For a local laboratory data disk:

```powershell
.\.venv\Scripts\python.exe -m cct.app --data-dir 'D:\CCT Records'
```

```sh
sh scripts/start.sh --data-dir '/Volumes/Lab Data/CCT Records'
```

Previously created records under the project's `data/` folder are preserved and are not automatically relocated. Open that existing collection with `--data-dir /absolute/path/to/project/data`, or copy the complete collection after the app is closed and recordings are finalized. SQLite databases are the authoritative records; CSV files are portable exports for analysis in other programs.

## Sharing records and controlling hardware

Each workstation should record to its own local disk. Export or copy completed experiment folders for a collaborator to review. In Results, **Import completed test** validates a finalized `run.sqlite` folder and makes a new local copy with provenance; the source is unchanged and controls never resume. Running records, active journals, unsupported schemas and malformed content are rejected. CSV exports can also be opened in ordinary analysis tools on other systems. Avoid pointing a live SQLite recording at a network share or a cloud-synchronized working folder. The app is not a multi-user server, and its local instance lock is not distributed control arbitration.

One computer owns the USB hub and the apparatus at a time. On another computer, use its native package or create its local source environment, install necessary USB drivers and import the reviewed apparatus profile. **Scan USB adapters** matches unique saved serial identities to current port names; COM numbers do not need to be manually rewritten. Check the physical cable associations and instrument responses. A changed cable pairing clears commissioning; an OS-assigned port-name change alone does not. Physical mappings, gas actions, limits, bridge firmware and acknowledgements still need a supervised acceptance check on each control workstation.

Phones, tablets, Chromebooks, and a web browser are not supported clients for this desktop interface. Remote viewing or browser access would require a separate read-only service and authentication design; a shared folder does not provide live remote control. A native one-folder Windows build is provided separately from the editable source. Signed installers, native macOS/Linux acceptance and mobile access remain future work. Build separately on each target OS with `scripts/build_desktop.py`; PyInstaller is not a cross-compiler.
