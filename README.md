# CCT Control

A local laboratory controller for **Zero, Adsorption, Breakthrough and Desorption** tests. The existing desktop interface remains available, and a new compact browser interface is the primary path for continued development.

## Browser interface

Run `Start CCT Web.cmd`, then open `http://127.0.0.1:8765`. The browser app includes:

- test registration with a generated, editable run name and required operator;
- live S3, LI-COR, Alicat and 146C connection cards;
- live LI-COR CO₂ and Alicat flow graphs;
- compact named valve, gas and heating dropdowns; and
- saved-run history.

The local server owns the serial ports. Do not start a second controller process against the same USB hub. The server binds only to this computer by default; authentication and TLS must be added before enabling LAN access.

The target architecture connects the computer directly to the S3. Real direct-S3 valve and heater commands are deliberately locked until the requirements in `docs/direct-s3.md` are implemented and commissioned. Simulation is available for interface development.

The interface has only **Tests, Live test, Results, and Connections**. There is no Recipes tab or general-purpose valve-control screen.

Connections contains only four device names and USB dropdowns. Available adapters refresh automatically while this page is open; the list pauses while a dropdown is open or devices are connected. **Connection options** in the top bar opens profile import, explicit connect/disconnect and setup instructions. The operator app has no simulator, speed control or fault-injection tools. Dropdown selections ignore mouse-wheel scrolling; open lists still scroll and keyboard selection remains available.

## Current setup: Core2 and LI-COR USB; Alicat and 146C manual

Select Core2 and LI-COR USB adapters. Choose **Operated manually — no app readings** for Alicat and **Operated manually — no USB control** for Thermo 146C. Starter settings use these modes. Set the physical 146C to Local/manual at its front panel. Manual devices have no app port, commands or readings. Confirm gas correction and continuously monitor actual Alicat flow externally; the app has no automatic flow-loss protection in this mode. Core temperature, S3 readback and LI-COR reading checks remain active. LI-COR can also be set to manual, in which case no CO₂ data are recorded.

Single Core tests support manual measurement devices. Repeating automatic cycles require USB CO₂ and flow measurements and are disabled when Alicat or LI-COR is manual. H₂O is not recorded, plotted or used in stability checks; LI-COR raw XML is not saved, so water values cannot leak into the event record. Existing historical records remain unchanged.

Before a hardware run, explicitly confirm the 146C is set correctly and the required supplies are available. The reviewed apparatus profile must establish that Core-controlled Air/N₂ routing can perform the chosen test without a 146C command. If a procedure needs an additional front-panel change, do it under the approved lab procedure; the app cannot infer or verify that change.

End test stops Core-controlled source valves and heat. It does **not** turn off the manually operated 146C; stop it separately as required. Manual mode is not a dry run: gas flow and heating are real.

## Supporting features

- Connections passively lists USB adapters and matches saved unique serial identities to the current computer's port names. It does not guess an unknown instrument or send trial commands to arbitrary ports.
- Four cable selectors keep the page simple. Profile review, wiring/read/control instructions, pairing confirmation and explicit connection stay available in Connection options. Missing, duplicate and unexpected identities require attention.
- Changed physical cable pairings create a new local profile and clear commissioning; a changed COM number for the same remembered adapter does not rewrite the imported profile.
- **What will happen?** explains the selected Core function. Cycle settings appear only for Repeating breakthrough cycles. **Review checks** summarizes readiness without gas or heat; hardware Start requires a physical-setup review and rechecks readiness.
- Settings are frozen while a test is active. Operators can save time-stamped observations without changing the procedure; observation markers appear on the CO₂ graph.
- Faults include a next-step explanation. Shutdown controls say **Shutting down…** while recording continues; continuous acquisition does not show a made-up completion percentage.
- Results can filter real/simulated records and import a completed test as a new local copy, without reconnecting equipment or overwriting the original.
- Offline help and privacy-conscious connection reports are available from the app. Reports omit measurements, profiles, serial identities, free-text notes and raw logs.

## Open the app

For a native Windows package, extract the entire ZIP and double-click **CCTControl.exe**, keeping its `_internal` folder beside it. Python and Codex are not required for that package; adapter drivers may still be needed. The build is unsigned and needs the lab's normal approval and another-computer acceptance check.

For the editable source version, double-click **Start CCT Control.cmd** in this folder. This workstation already has an isolated `.venv` with desktop dependencies installed.

On another Windows computer, install Python 3.11 or newer and double-click **Setup CCT Control.cmd** once before launching. Mac and Linux setup and launch scripts are included; see [desktop compatibility](docs/compatibility.md). Install the environment on each computer rather than copying `.venv`. Codex and a ChatGPT account are not required.

## First test with two USB connections

1. Select Core2 and LI-COR adapters in Connections, with Alicat and 146C manual. Import the reviewed apparatus profile and connect using Connection options. The compatible Core2 USB bridge must be installed and its S3 link verified.
2. On **Tests**, choose Zero, Adsorption, Breakthrough, or Desorption. Enter your notebook/test ID and sample details.
3. Basic Core tests have no cycle settings. Repeating cycles become available when both CO₂ and flow are measured over USB.
4. Review the physical setup and confirm external flow monitoring, then select **Start test**. Watch CO₂ and temperature while monitoring flow at the Alicat.
5. Select **End test**. Source valves and heating turn off through Core2/S3. After acknowledged output changes, the reviewed vent route is held for 10 seconds before saving completes. A hot column is still hot; temperature monitoring continues while connected. Review and export the saved test in **Results**.

| Test | What it does |
| --- | --- |
| Zero | Nitrogen bypasses the column to check the analyzer baseline. |
| Adsorption | Core column-to-exhaust route. Air starts **off**; use Air on / Air off. Analyzer CO₂ is not the column-outlet measurement in this route. |
| Breakthrough | Core air-bypass route. Use **Through column** or **Return to bypass**; the single test never switches itself. |
| Desorption | Core nitrogen-through-column route. Heater starts **off**; use **Heat to target**, **150 °C heat**, or **Heater off**. Nitrogen flow is required before heating. |
| Repeating breakthrough cycles | Automatic inlet reference, sustained cutoff, venting, regeneration and cooldown for the selected cycle count. |

Normal Core tests have **no percentage cutoff** and run until End test, subject to safety checks. The separately selected repeating-cycle function exposes its cutoff and repetition settings. Historical experiment settings remain readable and do not change the new Core-test controls.

Opening the app does not connect devices or start heating or gas delivery. Developer regression fixtures generate artificial data only inside the test tools; they are not operator controls.

## What is saved

Each test has its own unique folder and SQLite database in the user's local data folder: `%LOCALAPPDATA%\CCTControl` on Windows, with native equivalents on Mac and Linux. Test identity, exact settings, measurements and their age, commands, step changes, and faults are retained. CSV and JSON exports are readable on other computers. Reusing an ID does not overwrite an earlier test.

Continuous recording includes warmup, routing, baseline acquisition, breakthrough, regeneration, venting, and cooldown. Missing readings remain missing; they are not converted to zero. Historical records do not resume controls automatically.

Use `CCT_DATA_DIR` or the app's `--data-dir` option to put data in another local directory. Export or back up a completed run before copying it to shared storage.

## Connecting the apparatus

See [the hardware guide](docs/hardware.md) for serial adapters, the example hardware profile and separate Core2 USB bridge firmware. Original controller sources are preserved outside this application.

The original Core2 sketch prints diagnostic text and does not implement the computer command protocol. The new bridge must be compiled, reviewed and commissioned before automatic live operation. The app requires a compatible handshake and a complete commissioned hardware profile before enabling live control.

The example profile deliberately leaves apparatus-specific settings uncommissioned. In particular, the 146C Air/N2 command mapping, valve/vent routes, and heating-without-N2 procedure must match the installed plumbing. The Alicat B-500C is a BASIS meter: flow is measured there, rather than treating it as a flow setpoint controller.

**End test** performs a controlled stop while recording continues. Loss of required measurements or an unsafe temperature stops progression and records a fault. Ending before measurement begins is recorded as stopped, not a completed measurement. Software controls supplement independent physical protection and shutdown controls.

One computer owns the USB hub at a time. This is not yet a browser/mobile or simultaneous multi-user control system. Windows has been tested locally; Mac/Linux scripts and portable APIs are provided, but native acceptance testing and adapter-driver checks are still required before lab use on those platforms.

When changing computers, import the reviewed apparatus profile and select **Scan USB adapters**. Existing unique adapter identities resolve to the new computer's ports. Initial pairing still requires checking the physical cable labels. The app revalidates the identity immediately before opening a matched port and does not automatically reconnect or resume after a fault.

## Editing and maintaining the app

The code is intentionally divided by responsibility:

| Area | Files | Change here to… |
| --- | --- | --- |
| Test definitions | `cct/named_tests.py` | Edit test descriptions and explicit continuous/automatic defaults |
| Common definitions | `cct/contracts.py` | Add channels, units, or technical defaults |
| Experiment logic | `cct/engine.py` | Change phase sequence or completion rules |
| Data and exports | `cct/storage.py` | Change saved records and CSV organization |
| Simulator | `cct/simulator.py` | Add realistic scenarios or simulated faults |
| Application service | `cct/service.py` | Connect the UI, engine, storage and devices |
| Desktop interface | `cct/ui/` | Change screens, controls and plot appearance |
| Device protocols | `cct/drivers/` | Add or change instrument communication |
| Passive USB matching | `cct/drivers/discovery.py` | Change adapter inventory and fail-closed identity resolution |
| Operator help and plans | `cct/device_guides.py`, `cct/operator.py` | Edit supported wiring/help, test steps and recovery explanations |
| Portable records | `cct/portable.py` | Change completed-test import, profile copies and sanitized support reports |
| Local controller | `firmware/` | Change the Core2 USB bridge and local controls |
| Apparatus configuration | `config/` | Configure serial connections and reviewed command/route mappings |

Keep device-specific bytes and pin mappings out of screen code. Settings are frozen into each test record. Legacy `recipe.json` files and database keys remain readable for compatibility; new records also include a plainly named `test_settings.json`.

## Development

```powershell
.\.venv\Scripts\python.exe -m cct.app
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Dependencies are declared in `pyproject.toml` and `requirements.txt`. The tested dependency versions are recorded in `requirements-lock.txt`. See [architecture and design choices](docs/architecture.md) for the boundaries that keep future edits manageable.

Test-form labels and explanations live in `cct/ui/setup.py`; canonical test descriptions are in `cct/named_tests.py`. Results loading and CSV export use background workers, so live acquisition continues while reviewing older data. Simulation/hardware mode and test identity are included in measurement exports.

To create a source ZIP for another computer, run `python scripts/package_source.py`. It includes setup scripts and documentation but excludes measurements, local hardware profiles, logs, virtual environments and build artifacts.

To build a native desktop package, install `requirements-build.txt` and run `python scripts/build_desktop.py`. Build separately on each target OS; this script is not a cross-compiler. Timestamped builds never replace a previous package.

Human-interaction decisions and remaining physical acceptance work are documented in [product design](docs/product-design.md).

See [verification results](docs/verification.md). To repeat the full operator workflow and generate interface screenshots without physical equipment:

```powershell
.\.venv\Scripts\python.exe scripts\verify_desktop.py
.\.venv\Scripts\python.exe scripts\verify_product.py
```
