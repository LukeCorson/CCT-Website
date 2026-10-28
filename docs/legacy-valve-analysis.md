# Legacy valve and cycle analysis

This analysis reconciles the original controller sketch with the current Core2 USB bridge and Python automation. It records evidence; it does not commission a valve mapping.

## Source reviewed

- Original sketch: preserved as a formatting-normalized source copy at `legacy/CCT_Version2.0.ino` after discovery in the workstation Downloads folder.
- SHA-256 of the discovered original: `E2D9AA228B1B26CA1F10D1535404F2C20024BE78C9AC63D662EA76C34EF43AC8`.
- Current bridge: `firmware/src/main.cpp` and `firmware/src/stable_uart.cpp`.
- Host automation: `cct/engine.py`, `cct/core_tests.py`, and `cct/drivers/hardware.py`.

The original sketch targets an Arduino-style board with direct GPIO and blocking delays. It does not accept USB commands; its serial port only prints diagnostic text. The current Core2 bridge is the component that adds a safe, versioned USB command protocol.

## Original direct outputs

| Legacy name | GPIO pin | Meaning proven by code |
| --- | ---: | --- |
| Heater | 2 | Relay output controlled from MAX6675 temperature feedback |
| Gas valve | 23 | Set high only by Adsorption and Breakthrough final states |
| Zero valve | 25 | Set high only by Zero final state |
| Flush valve | 27 | Set high only by Zero final state |
| Carrier valve | 29 | Set high only by Desorption final state |
| Release valve | 31 | Set high by Zero, Desorption, and Breakthrough final states |

`HIGH` means the controller energized that output. Relay polarity and the physical normally-open/normally-closed position still require inspection.

## Original final states

The bit string below is ordered `gas, zero, flush, carrier, release`.

| Function | Final state | Energized legacy valves |
| --- | --- | --- |
| Idle / Control | `00000` | none |
| Zero | `01101` | zero, flush, release |
| Adsorption | `10000` | gas |
| Desorption | `00011` | carrier, release |
| Breakthrough | `10001` | gas, release |

## Original transition sequences

Each function first de-energizes outputs in a deliberate order:

1. Flush low.
2. Carrier low, then wait 3 seconds.
3. Gas low, then wait 1 second.
4. Release low, then wait 1 second.
5. Zero low.

It then applies the selected final state:

- Zero: zero high; wait 1 second; release high; wait 2 seconds; flush high.
- Adsorption: gas high; wait 200 ms.
- Desorption: release high; wait 2 seconds; carrier high.
- Breakthrough: gas high; wait 1 second; release high; wait 200 ms.

These delays are evidence of required make/break ordering. They must not be collapsed into a single raw output write without reviewing the plumbing and relay behavior.

## Heater sequence in the original sketch

The MAX6675 uses pins 4, 5, and 6. Heater output is pin 2. The physical switch on pin 48 starts and stops heating.

1. Heat until 115 °C.
2. Regulate around 115 °C for up to 5 minutes, or advance early if temperature exceeds 140 °C.
3. Heat to 140 °C.
4. Maintain using an on-below-130 °C/off-at-or-above-130 °C hysteresis.

Comments in the sketch disagree with the executable thresholds, so the code values above are authoritative for reproduction. The current bridge has a different configurable target algorithm; that difference must be an explicit commissioning decision.

## Current S3 output map

The current bridge documentation records seven S3 bits:

| S3 bit | Current label |
| ---: | --- |
| 0 | Exhaust / LI-COR outlet |
| 1 | Check / bypass |
| 2 | Bypass |
| 3 | MFM selector / dynamic calibrator |
| 4 | Nitrogen source |
| 5 | Compressed-air source |
| 6 | Heater |

The legacy sketch has five named gas-path valves, while the S3 map has four route bits plus two source bits. Names alone do not prove equivalence. In particular, `gas valve` cannot safely be assumed to be the same device as either S3 source bit, and `release valve` cannot safely be assumed to be the S3 exhaust bit without tracing wires and tubing.

## USB control path that is implemented

The supported path is:

`browser → local FastAPI server → LabService → HardwareRig → Core2 USB JSONL v1 → S3 CRC UART v1 → relay outputs`

The browser sends only named actions. `HardwareRig` sends `claim`, heartbeat, named `route`, named `gas`, heater target, and safe-state commands to the Core2. The Core2 preserves source/heater bits during route steps, confirms S3 electrical readback, regulates temperature locally, and applies the reviewed safe vent mask when the host heartbeat is lost.

The Python automatic breakthrough engine already runs the cycle sequence over that command path:

1. Analyzer warmup and safe state.
2. Optional initial desorption or purge.
3. Cooldown.
4. Air bypass stabilization and inlet baseline.
5. Switch to the column and measure breakthrough.
6. Close feed and vent/depressurize.
7. Heat/purge regeneration.
8. Cool down and repeat, or perform final shutdown.

## Remaining commissioning work

Before physical USB control, trace every legacy valve wire to the actual S3 relay channel and verify the tubing destination. For each named function, record the S3 applied mask after every safe transition step. Only then should those reviewed steps be entered into `config/hardware.local.json` and the profile marked commissioned.
