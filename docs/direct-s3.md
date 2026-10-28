# Direct S3 control contract

The working hardware path uses the Core2 USB bridge to control the S3. A future direct computer-to-S3 USB connection is not yet executable. The Core2 currently supplies temperature sensing, heater regulation, heartbeat shutdown, named route sequencing and a USB ownership handshake. Those protections must move to the S3 firmware or another independently commissioned controller before direct actuation is enabled.

## Required before hardware enablement

1. A versioned USB protocol with an identity handshake and exclusive-session claim.
2. A local watchdog that applies the commissioned safe vent state when the host heartbeat is lost.
3. Electrical output readback with command acknowledgement and mismatch latching.
4. Thermocouple acquisition, open-sensor detection and a locally enforced maximum temperature.
5. Local closed-loop heater regulation. The web server may request a target but must not pulse the heater relay.
6. Named, reviewed route transitions. The browser and API must never accept arbitrary output masks.
7. Source-valve mutual exclusion and an independently usable local stop.
8. A commissioned profile containing the verified vent mask, route truth table, relay polarity, temperature limit, and evidence for shutdown testing.
9. Automated protocol, watchdog, over-temperature, sensor-fault and readback-mismatch tests.
10. A supervised dry electrical acceptance test followed by a documented apparatus acceptance test.

Until every item is complete, hardware calls from the web manual-control endpoint fail closed. The simulator remains enabled for interface development.

## Existing wire information

The preserved Core2 bridge uses CRC-framed UART v1 with `0xA5 0x5A`, protocol version 1, `SetOutputs`, `Status`, and `Ping` frames. Existing output meanings are documented in `docs/hardware.md`. They are historical evidence, not permission to energize outputs directly.
