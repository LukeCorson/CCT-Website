# Verification of CCT Control 0.5.0

Verified locally on Windows on October 27, 2026. Physical apparatus commissioning is separate from these results.

## Results

- 168 automated tests passed, including two-USB operation (Core2 + LI-COR), independently manual Alicat/LI-COR/146C modes, no manual device workers or commands, null measurements, explicit external-flow monitoring confirmation, automatic-cycle restrictions, ten-second vent hold after acknowledgements, hot-column warnings, S3 link-loss faults and matching wire acknowledgements. H2O is excluded from parsing, nested sample recording and plots. Operator simulation controls and the demo CLI were removed; internal regression fixtures remain.
- The actual desktop workflow verified all four navigation pages and four test selections, no visible or active percentage cutoff on normal tests, and explicit opt-in for repeated automatic breakthrough cycles.
- The normal Breakthrough test starts in bypass and changes route only when the operator chooses Through column. The latest internal regression walkthrough retained 552 samples across 551 fixture seconds, 15 events, frozen settings and CSV exports; there was no concentration cutoff in this Core test.
- A separate explicitly enabled automatic sequence completed two cycles including initial desorption, heating, cooldown, inlet references, breakthrough, venting and regeneration.
- A separate injected overtemperature fault stopped its run and preserved the completed experiment.
- Dependency consistency and Python compilation checks passed.
- PlatformIO successfully compiled the new Core2 USB bridge with optional named-test routes: 30,020 bytes RAM and 450,101 bytes flash reported by the toolchain.
- The real Qt interface was rendered and visually inspected: Tests, live data, Connections, Results, optional automatic settings and fault display. Measurements retain their stated units even near zero.
- PowerShell and POSIX launcher syntax and mocked Windows/macOS/Linux data paths were checked. Native Mac/Linux UI and USB operation have not been tested.
- The source ZIP has an allowlisted file manifest and SHA-256 hashes. Privacy checks exclude experiment records, local apparatus settings, logs, virtual environments and build output. Copying completed records to another data collection preserves review without reconnecting equipment.
- The native build script checks its frozen startup screen before publishing a ZIP. Prior startup dependency issues were resolved by excluding an unrelated build-environment ICU DLL when Qt uses the Windows system ABI. This is local-machine validation, not another-computer or real-apparatus acceptance.

The repeatable desktop verification report and rendered views are under `output/verification/`. Generated QA data stay there rather than appearing in the user's normal experiment library.

## Failure behavior exercised

- Opening the application does not connect devices or begin an experiment.
- Invalid metadata, non-finite numbers and incomplete hardware commissioning cannot start a run.
- Start also checks that every required reading is finite and fresh, even if a rig reports itself ready. Ending while initial test commands are still awaiting acknowledgement cannot be counted as a completed measurement.
- Cached/stale observations cannot satisfy breakthrough confirmation.
- Required sensor loss, excessive temperature, measured flow loss, S3 output-link loss, stage timeout and recording failure stop progression. Manual Alicat has no automatic flow-loss detection and requires continuous external monitoring; a manual LI-COR records no CO2.
- Test runs lock service-level manual actuation; End test remains accessible across pages. There is no Recipes or Manual controls tab in the operator UI.
- Core Adsorption starts with air off; Core Desorption starts with heating off. The operator uses only the controls belonging to that test. Core Breakthrough starts in bypass and never changes route automatically. Repeating cycles are a separate selection.
- A manually operated 146C does not need a port or status response; no 146C commands are generated during gas changes, Stop or fault shutdown. Meter-correction acknowledgements and real-flow/temperature protection remain active.
- Gas changes close physical source valves, verify instrument settings, then open the selected source. A failed or late acknowledgement after Stop cannot reopen the source.
- Incomplete records reopen as interrupted and never resume control automatically.
- Delayed history reads return on the UI thread without blocking acquisition. An older selection cannot overwrite a newer selection.
- History display reduction retains narrow peaks and missing-data gaps; original data and exports remain unchanged.

## Hardware limits

No firmware was uploaded and no heater, valve or instrument was operated. No real-device calibration, pinout, flow arrangement, gas/span mapping, vent route or physical shutdown behavior is certified by the tests. The app therefore requires the compatible Core2 bridge, actual device identities and a reviewed apparatus profile before live operation. See `docs/hardware.md` for the commissioning procedure and the settings still requiring confirmation.
