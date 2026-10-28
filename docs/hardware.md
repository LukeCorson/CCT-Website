# Hardware connection and commissioning

The application starts disconnected. Real instrument integration is implemented,
but no real apparatus has been operated, flashed, or validated by this build.
Existing controller sources outside this application are unchanged. Use a supervised
commissioning session before permitting an unattended recipe.

## Connections

One control computer is the USB host. Leave the 146C, LI-840A, Alicat B-500C
and Core2 attached to the data hub, and plug the chosen computer into the hub's
upstream USB cable. Each endpoint needs its own serial port and unique adapter
identity. Windows uses COM names; Mac and Linux use device paths.
The Core2 continues to talk to the S3 through the existing CRC-framed UART v1.
For two-USB testing, use Core2 and LI-COR over USB with Alicat and 146C manual.
Manual Alicat/LI-COR ports stay closed, no commands are sent, and their missing
measurements remain null. Manual flow requires per-run confirmation of continuous
external monitoring and disables unattended repeated cycles. H2O is not recorded.
End test sends the reviewed vent mask (source bits 4/5 and heater bit 6 OFF), waits
for S3 electrical readback, then holds for 10 seconds. It does not certify cooling.
Close other terminal/vendor applications before connecting; only one program
may own each serial port.

| Instrument | Interface implemented | Default serial settings |
| --- | --- | --- |
| Core2 | New `cct-usb` JSONL protocol v1 | 115200, 8N1 |
| LI-840A | XML `LI840/DATA` CO2, CELLTEMP; H2O ignored | 9600, 8N1, no flow control |
| Alicat B-500C | BASIS labelled ASCII; meter only | 38400, 8N1, address A |
| Thermo 146C | Existing ID 51 / byte 0xB3 commands | 9600, 8N1 |

The LI-840A needs its null-modem/crossover serial cable. The BASIS connector has
an instrument-specific pinout; do not assume a standard DB9 straight cable.
Verify the actual Alicat interface and baud rate from its label/settings.
The official legacy BASIS manual is shared with controller products and shows
an optional `SP` setpoint field. The parser accepts meter frames without `SP`,
for example `A 25.0C 0454.2SCCM Air`, and the labelled controller example too.
It rejects generic M/MC numeric-column frames. If the particular B-500C returns
another format, capture it and extend the parser with a test before use.

Primary references:

- [Alicat technical-reference index: B-Series meter and legacy manual](https://www.alicat.com/technical-reference/)
- [Alicat legacy BASIS manual: pinout, 38400 baud, labelled data, gas correction](https://documents.alicat.com/manuals/noindex_legacy_basis.php)
- [LI-COR LI-840A description and supplied null-modem cable](https://shop.licor.com/env/support/LI-840A/topics/description.html)
- [LI-COR LI-840A communication software and RS-232 operation](https://shop.licor.com/env/support/LI-840A/topics/software.html)

## Local profile

### 146C operated manually — real gas flow

`calibrator_mode: "manual"` excludes the 146C from required USB identities,
workers, readiness checks and all command transactions, including Stop. Set its
physical Local/manual mode at the front panel; the app does not issue a guessed
remote/local-mode command. The 146C can remain physically connected, but this app
does not open that serial endpoint in manual mode.

This mode is **not** an electronics-only rehearsal. Core-controlled valves,
heating, LI-COR measurements and Alicat flow/gas correction remain live. Gas
changes still close Core source valves, verify meter correction, then reopen
the requested source. Real flow, sensor freshness, temperature limits and local
shutdown remain mandatory. Verify the physical Air/N₂ supply arrangement and
gas-selection responsibilities before commissioning the profile.

The operator confirms the front-panel setting and available supplies before
each hardware start. The frozen configuration records this acknowledgement and
the manual-calibrator mode. End/Stop closes Core-controlled valves and heat,
but cannot shut off the manually operated 146C. Use its front panel or the lab's
physical shutdown procedure separately as required.

Omitting `calibrator_mode` retains the original automatic, four-device profile
behavior. Switching modes in Connections clears commissioning and saves a new
local profile rather than overwriting the imported one.

In Connections, import the apparatus's reviewed profile or choose Create starter
settings. Scan USB adapters, check labelled cable assignments and save any new
pairing as a separate per-user profile. The imported file is not changed. A changed
physical binding clears commissioning. Starter settings remain deliberately
incomplete: setting only `commissioned` to true does not enable commands.

For engineering/source workflows, copying `config/hardware.example.json` to
`config/hardware.local.json` remains supported. The local file is ignored by Git.
An explicit profile path may also be passed to `HardwareRig(path)`.

Passive discovery lists USB/serial adapters without opening ports or transmitting
queries. Saved unique `expected_usb_serial` identities are resolved to current
port names on each explicit connection, optionally constrained by
`expected_usb_vid` and `expected_usb_pid`. The worker repeats the identity check
immediately before opening. The original profile and runtime-resolved profile
are kept separately; a changed COM number alone does not change the apparatus
configuration. Missing, ambiguous, duplicate or malformed identities stay blocked;
there is no fallback to a stale port number or guess based on the adapter's name.

An adapter without a stable unique serial number needs replacement or a reviewed
extension for another stable identity; removing this check is not a commissioning
shortcut. An adapter identity does not uniquely identify the instrument behind
its serial cable. Initial physical pairing is deliberate; supported protocol
checks follow Connect, and do not claim unique model/serial identification where
the instrument does not provide it through the implemented interface.
Read-only connection and telemetry are permitted while commissioning is incomplete.
Connection does not apply valve masks, start heat, calibrate an analyzer, change
LI-COR heater settings, tare the flow meter, or select a calibrator gas.

Per-device instructions are available within Connections and the offline operator
guide. A local connection report intentionally excludes raw logs, measurements,
profiles, serial identifiers, notes and personal paths.

The LI-COR worker consumes any existing XML stream and polls DATA when no fresh
stream arrives. Ensure XML stripping is disabled and CO2 and CELLTEMP are enabled
using the vendor tool. The application never sends arbitrary XML or calibration
commands. `licor_output_config()` is a restricted helper for a future explicit
configuration workflow; it is not automatically sent on connect.

Before enabling commands, record the commissioning operator, date, plumbing
evidence, gas mapping evidence, heater test and shutdown evidence. The profile is
frozen in each run's metadata, alongside instrument settings and initial outputs.

### Routing and physical source valves

The existing S3 electrical output bit order is:

| Bit | Output | Existing meaning when high |
| --- | --- | --- |
| 0 | Exhaust valve / GPIO9 | LI-COR outlet |
| 1 | Check valve / GPIO8 | Bypass |
| 2 | Bypass valve / GPIO7 | Bypass |
| 3 | MFM selector / GPIO6 | Dynamic gas calibrator |
| 4 | Nitrogen / GPIO5 | Source open |
| 5 | Compressed air / GPIO4 | Source open |
| 6 | Heater / GPIO10 | Heater energized |

These labels reproduce existing firmware. Verify actual relay polarity, tubing,
which branch contains the meter, and every destination on the apparatus. Electrical
readback reports the S3's applied output state, not physical valve position or
measured flow; verify those separately.

Each named route (`bypass`, `column`, `vent`, `purge`, `closed`) contains one to
eight steps: `{"mask": 0..15, "wait_ms": 0..10000}`. Only routing bits 0..3
belong in a route; source bits and heat are independent. Named routes preserve
current source/heater state. The example contains empty routes because the
final vent route and complete truth table have not been confirmed.

Two optional basic-test routes, `zero` and `adsorption`, are also supported.
They may remain empty in an otherwise commissioned profile; only those tests
then remain unavailable. `zero` is the reviewed N2 bypass-to-analyzer path.
`adsorption` preserves the legacy Core2 air/column-to-exhaust test, distinct
from `column` for breakthrough measurement to the LI-840A. Review and fill their
steps before enabling those particular tests; no executable masks are supplied.

Existing candidate routing transitions, stripped of source bits, are recorded
here for review rather than installed as executable defaults:

- Bypass preparation: `1, 3, 7, 15` with established one-second spacing and the
  original three-second gas-selection delay.
- Column from bypass: `13, 9`, originally one second apart.
- Bypass from column: `13, 15`, originally one second apart.
- Desorption preparation: `1, 1, 1, 1` with established one-second spacing and
  three-second selector delay.

These are state-dependent legacy sequences, not a universal route truth table.
Review each proposed route from **every** allowed predecessor. `closed` describes
the reviewed routing position; the engine separately commands gas off.
`safe_vent_mask` must match the final verified vent mask, stay within 0..15, and
therefore have heater and both physical source valves off.

### Gas selection

The user identified the 146C as selecting the Air/N2 condition, but the exact
span configuration is unconfirmed. Only the previously demonstrated commands
`sel gas a span 0`, `sel gas a span 1`, and `sel gas off` are executable. No
imaginary `sel n2` command is supplied. Set `air` and `n2` only after verifying
which span setting corresponds to each actual gas condition. If the physical
146C arrangement requires other commands, extend the allowlist with primary
protocol evidence and tests instead of inserting raw serial text in a recipe.

Record distinctive literal `status_identity_contains` and `status_contains`
readback strings for all three gas actions. An echo or successful serial write
does not count as completion: a subsequent status response must verify the
requested configuration. Use different status markers for each state.

`gas(air/n2)` is a three-phase transaction: close both Core2 source valves and
wait for electrical acknowledgement; configure 146C and meter and wait for both
readbacks; then open the selected physical source valve. `gas(off)` closes the
Core2 sources and selects calibrator off. Routing never silently reopens a source.
This makes “heat with N2 off” explicit and testable.

The B-500C is treated as a meter, never given flow setpoints. For verified BASIS
firmware, gas correction is Air=0 and N2=3. Enable `gas_selection_verified` only
after confirming this on the actual unit. A gas-change command must be followed
by a labelled measurement reporting the expected gas before it completes. Record
the meter's calibration gas and standard temperature/pressure. A corrected
standard-volume reading is not a direct volumetric flow at the column conditions.

## Core2 bridge firmware

The new PlatformIO project is in `firmware/`. Build with:

```powershell
pio run -d firmware
```

This builds only. Firmware uploading is a separate supervised action and has
not been done. The existing S3 firmware can remain in place because UART v1 is
unchanged. The bridge keeps existing sensor pins: S3 UART RX/TX 13/14,
MAX6675 SCK/SO/CS 18/38/26, ambient SDA/SCL 32/33.

USB uses one JSON object per line with `protocol:"cct-usb"`, `version:1`, `op`,
unique `id`, and session token. Operations are hello/status/claim/heartbeat/release,
route/gas/heater/valve/safe_state. The `valve` operation accepts only one of the
six documented valve names plus a Boolean on/off state; neither the browser nor
the host API accepts a raw output mask. Core2 also prevents nitrogen and compressed
air from being opened together. A read-only hello exchanges no output commands.
Claim validates the route table and local temperature/vent limits, with no
actuator action. Only the owner may command outputs. Acknowledgement means the
route's waits have finished and S3 electrical readback matches, not merely that
an action was queued. Recent IDs are deduplicated in the current session.

The bridge regulates heat locally, retaining the existing custom target-minus-15
preheat/60-second dwell and special 150 C profile (130 C preheat, median coast
decline, then 150 C hold). The PC chooses a target; it does not toggle heater
relays at USB timing. Targets must be at least 50 C and below the reviewed hard
limit (maximum 175 C). The simple Core2 screen shows status and local Stop.

Loss of the computer heartbeat for three seconds cancels routing, stops heat,
closes both source valves and applies the configured vent. Thermocouple failure,
temperature limit, local Stop, readback mismatch or transaction timeout latches
a fault. The original S3 watchdog independently turns the heater off after
two seconds without Core2 commands while retaining valve outputs. A failed UART
cannot guarantee a valve move, so verify the actual loss-of-power/link behavior
and any independent hardware shutdown provisions during commissioning.

The app never automatically resumes after disconnect or reboot. Stop/emergency
commands cancel pending gas-change groups and preempt normal serial queues so a
late acknowledgement cannot reopen a source. App tick starvation also stops the
worker's heartbeat, allowing local shutdown even if the UI process freezes.

## Supervised acceptance checklist

1. Start without heaters or gas supply energized, label all USB cables and match
   each identity/COM port. Confirm correct analyzer values and meter units.
2. Build and review the bridge; perform firmware upload only as an explicit,
   supervised commissioning step. Verify legacy firmware is rejected by the app.
3. Verify valve truth table/polarity and every route transition without sample
   exposure; record evidence in the profile. Verify the actual vent destination.
4. Verify 146C Air/N2/off status readbacks and BASIS gas correction. Test close-
   configure-open ordering and sustained flow after routing settles.
5. Verify thermocouple open detection, temperature limit, local Stop, computer
   loss, Core2–S3 link loss and readback faults. Verify physical behavior as well
   as electrical state.
6. Run one supervised complete experiment and inspect the saved raw frames,
   timestamps, route events, inlet reference and CSV exports before repeated runs.

No software check or compile substitutes for these physical acceptance tests.
