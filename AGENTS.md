# Repository instructions

- Treat this repository as the source of truth. Preserve unrelated user changes.
- Run the full unittest discovery command before completing code changes.
- Browser UI work belongs in `cct/web`; apparatus logic belongs behind `LabService`.
- Never add a web endpoint that accepts a raw S3 output mask, arbitrary serial text, or heater relay duty cycle.
- Hardware actuation must remain fail-closed until `docs/direct-s3.md` is satisfied and the profile is commissioned.
- Bind the web server to loopback by default. Do not expose it to a LAN or the internet without authentication and TLS.
- Do not commit `data/`, `config/hardware.local.json`, credentials, USB serial identities, or experiment records.
