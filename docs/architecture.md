# Architecture and maintenance

## Ownership

Only the experiment engine decides when an experiment advances. A widget requests an operation through `LabService`; it does not open a serial port, write a file, or toggle a pin. A rig translates named operations into simulated behavior or device commands.

The computer owns test metadata, cross-device decisions, plotting and the durable record. The Core2 owns the local temperature loop and timed physical routing. The S3 applies its output mask and returns its electrical state. Electrical readback is not a physical valve-position sensor. Ambient BMP180 pressure is not column pressure.

The operator chooses one of four tests from `cct/named_tests.py`. Named tests are continuous by default: End test requests controlled shutdown. Only an explicit automatic-breakthrough choice enables a percentage cutoff, regeneration and repeated cycles. Legacy saved settings without a test type retain their original documented behavior; they are not used as the new form's defaults.

## Interfaces

`cct/contracts.py` documents the nonblocking `Rig` interface and channel names. All serial I/O runs in device workers. Reading a rig snapshot never waits for serial bytes. Commands, acknowledgements and failures are surfaced as events and recorded.

An internal deterministic rig implements the same interface for regression tests. The operator app offers no simulator, speed control or demo command. Artificial fixture outcomes are not hardware validation results. Manual device modes remove those USB workers and required measurement channels without inventing data; Core2 and its S3 readback remain mandatory. Automatic cycles require measured CO2 and flow. H2O is omitted from channels, stability checks, new samples, plots and LI-COR raw event recording.

## Time and data

Elapsed experiment time controls holds and timeouts. Wall-clock timestamps identify observations in UTC. Original timestamps, values, units and validity are retained alongside the convenient one-second combined view. A recent ambient observation can be reused in that view with its age shown; a stale observation cannot be used to qualify stability.

Test settings are copied into the record at start, with `test_settings.json` alongside legacy `recipe.json` for compatibility. A cycle's inlet reference is frozen from its accepted baseline window. Reprocessing must not overwrite original measurements or silently replace their settings.

## Failure behavior

Opening the application, connecting a port, or recovering an interrupted database does not restart an experiment. A live run requires compatible devices, fresh required measurements, commissioned apparatus settings, and an acknowledged controller session.

Normal stop, measurement fault and application loss are different events. The local controller needs an independently timed heartbeat response because a computer cannot send a shutdown command after its own power or USB link has failed. The exact feed/vent route belongs to the reviewed apparatus profile.

Every stage has a maximum duration. An elevated flat CO2 signal does not prove regeneration. A sensor gap does not look like zero concentration. Early breakthrough crossings remain in the record even when a user configures a delayed stop guard.

## Adding a feature

1. Add a setting or channel to the shared contract with explicit units.
2. Implement the experiment or driver behavior independently of widgets.
3. Extend the simulator or recorded protocol fixtures.
4. Add a focused test for the actual failure or behavior being introduced.
5. Expose the setting in the relevant screen with a clear label and useful bounds.

Commission firmware and hardware profile changes separately from UI appearance changes. Keep the local apparatus profile out of source control; use the example profile to document its structure.
