# Product design and human interaction

The goal is a lab operator who can understand the next action without knowing a COM port, protocol grammar or valve bit mask. There are still only four main screens: Tests, Live test, Results and Connections.

The current test picker is deliberately limited to the four operator-controlled Core procedures plus a separate Repeating breakthrough cycles function. Single Core tests do not add automatic analyzer-baseline, route-change or heating decisions. Adsorption starts with air off, Breakthrough starts in bypass, and Desorption starts with heat off. The shared stale-reading, flow, temperature and controlled-stop protections remain active.

The requested operating arrangement uses real gas and a manually operated 146C. The app's manual-calibrator choice does not send an instrument-mode command: the operator sets Local/manual at the front panel and confirms the available supplies. No 146C port is opened, monitored or commanded; Core valves/heating and the other instruments remain active. End test cannot turn off the manually operated 146C.

## Design rules implemented

1. **Recognition before recall.** Four named tests have plain-language purposes and an expandable step preview. Device cards carry their own wiring, readings, supported controls and caveats.
2. **No hidden actuation.** Opening the app, scanning USB, reviewing checks, importing a record and saving pairing do not deliver gas or heat. Connect verifies communication; Start is separate. Hardware Start has an explicit physical-setup acknowledgement, followed by fresh backend validation.
3. **States mean what they say.** An adapter match is not an instrument identity, and an electrical acknowledgement is not proof of a valve's physical position. OFFLINE, SIMULATION, USB matched, instrument communicating and blocked control are distinguished in words rather than color alone.
4. **Progress must be honest.** Continuous measurement has no completion fraction. Actual timed holds show their progress; shutdown remains visible while cooling and recording continue. The normal test has no concentration-percentage stopping rule.
5. **Prevent mistakes before explaining them.** Missing or ambiguous adapters are not guessed. Current settings are locked during a test. Physical reassignment clears commissioning. Sample identity, fresh measurements, device readiness and recording failures are checked before or during operation.
6. **Make recovery actionable.** Faults retain data, explain what to check and never resume automatically. The operator can open Connections or offline help without losing the visible fault.
7. **Treat observations as observations.** Operators can annotate the current time without changing test settings. Notes are saved as events and graph markers, not interpreted as commands. Imported text is displayed as plain text, never HTML.
8. **Collaborate without sharing control.** Completed-test import makes a new local record with provenance. It never overwrites the source or resumes equipment. Practice and real data are labelled and can be filtered separately.

## First-time workflow

Connections contains only four device names and their USB dropdowns. Passive enumeration refreshes while this page is visible and pauses during selection or active connections. Closed dropdowns throughout the app ignore the mouse wheel; popup lists retain scrolling and keyboard navigation. The 146C row includes an explicit manual-operation choice.

Open Connection options in the top bar to import the apparatus's reviewed settings. Match labelled cables in Connections; save and connect explicitly in Connection options. Alicat, LI-COR and 146C each support manual operation without app commands or readings. The current setup needs only Core2 and LI-COR USB endpoints. Manual flow monitoring requires an explicit per-run acknowledgement and disables automatic cycles; absent values remain null. The operator app has no simulator controls. This separate window is modeless so End test remains reachable. Unknown or duplicate identities require correction. Initial unlabelled adapters can be labelled one at a time during approved safe-idle setup; never unplug a running test to identify a cable.

After connecting, review the actual instrument communication. A starter profile remains non-executable until a qualified operator has reviewed the gas mapping, routing, heating and shutdown behavior. Adapter pairing is not a shortcut around that review.

Choose a test, identify the sample, read the test preview and review checks. For a real run, acknowledge the physical setup before Start. The app revalidates on Start because the state may have changed while the review was open. End test requests controlled shutdown and keeps saving; it is not a certified hardware emergency stop.

## Moving to another computer

Leave instruments and labelled adapters attached to the hub. Connect one control computer to the hub's upstream cable at a time. Use the native package or install the source environment on the target OS. Import the reviewed apparatus profile. Unique saved USB serials, optionally constrained by VID/PID, are resolved to that computer's current port names. The imported file remains unchanged.

USB identity is not a cryptographic instrument identity. Moving the instrument-side cable to another instrument cannot be proven correct from the adapter's identity alone. Keep labels and physical associations intact, and perform an appropriate supervised check on each workstation.

## Deliberate limits

- No arbitrary active scanning or guessing commands against unknown serial ports.
- No remote browser/mobile control or simultaneous control by several computers.
- No automatic calibration, flow tare, guessed 146C mapping or flow setpoints for a meter.
- No claim that Mac/Linux hardware operation or a second physical computer has been tested here.
- No claim that a successful simulation, software test or compiled bridge certifies a physical apparatus.
- Native builds are unsigned. Driver installation, signing and institution-specific deployment approval remain separate work.

## What needs real-world user testing next

Observe a first-time operator pairing the actual labelled cables and starting a supervised Zero test. Check whether they can distinguish practice from real data, identify which readings represent the column outlet, find End test, understand waiting/cooling, and recover from a deliberately disconnected measurement device using the approved procedure. Record confusion and time-to-recovery before expanding features.

Before a real cycle, verify the 146C Air/N₂/off mapping, actual Alicat variant and gas correction, all plumbing transitions, local controller firmware, independent shutdown protection and loss-of-link behavior. These are physical acceptance tasks, not UI settings that the app can infer.
