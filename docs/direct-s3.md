# Direct S3 control

The application now targets a direct computer-to-S3 USB connection. The firmware supplied by the user cannot receive USB commands: it starts `Serial` but reads only `coreLink`, the software UART on pins 9 and 10. `firmware/s3_direct_usb/s3_direct_usb.ino` is the compatible direct-USB replacement.

The direct path currently supports only the six capabilities proven by the supplied source: Valve 1 through Valve 6, each off or on. The host sends the supplied `A5/state/checksum` frame and requires the supplied `5A/state/counter/checksum` acknowledgement. The replacement firmware also turns every relay off when host heartbeats stop.

There is no heater, temperature sensor, gas identity, route identity, or automatic cycle support in the supplied firmware. The UI states this explicitly and does not send those commands.

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

Until every item is complete, automated tests and heating remain locked. Manual Valve 1–6 control is allowed only through the strict named-valve API and confirmed firmware replies.

## Existing wire information

The preserved Core2 bridge uses CRC-framed UART v1 with `0xA5 0x5A`, protocol version 1, `SetOutputs`, `Status`, and `Ping` frames. Existing output meanings are documented in `docs/hardware.md`. They are historical evidence, not permission to energize outputs directly.
