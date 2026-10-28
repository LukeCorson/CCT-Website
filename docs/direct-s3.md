# Core2 control boundary

Direct computer-to-S3 control has been retired. The computer connects to Core2 over USB; Core2 owns the local temperature loop and sends CRC-framed output commands to the S3 over UART. The browser uses named routes and temperature targets and never accepts a raw output mask, arbitrary serial text, or heater duty cycle.

The S3 remains an internal relay controller. Its electrical readback confirms applied output bits, not physical valve position or gas flow. The Core2 firmware reads the thermocouple, rejects invalid targets, enforces the commissioned maximum temperature, requires an owned host session, and enters the reviewed safe state on heartbeat loss, sensor fault, readback mismatch, or local STOP. Supervised manual control addresses each valve by its documented name and Boolean state; no web request can supply a raw mask.

## Required before hardware enablement

1. Upload and verify the versioned Core2 USB bridge in `firmware/`.
2. Preserve the CRC-framed Core2-to-S3 UART link and verified electrical readback.
3. Commission every named route, including its safe transition from every allowed predecessor.
4. Record the reviewed vent mask, relay polarity, plumbing evidence, heater test, temperature limit, and shutdown evidence in the local hardware profile.
5. Verify thermocouple open-sensor detection, over-temperature shutdown, local STOP, computer heartbeat loss, and Core2-to-S3 link loss.
6. Verify the physical valve and gas-flow result independently; software readback is not proof of motion or flow.
7. Keep LI-COR, Alicat, and 146C connections separate unless their wiring and protocols are deliberately added to Core2 and tested.

Until the local profile is commissioned, the hardware connection remains read-only and actuation stays fail-closed. Simulation remains available for interface and procedure testing.
