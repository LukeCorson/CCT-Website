# Contributing

This repository is the source of truth for the CCT controller. Each person or ChatGPT account works from a branch and proposes a pull request; do not exchange edited ZIP files as competing master copies.

## Suggested workflow

1. Pull the latest default branch.
2. Create a short branch such as `web-live-feed` or `s3-protocol-tests`.
3. Keep one feature or safety change per pull request.
4. Run `python -m unittest discover -s tests -p "test_*.py"`.
5. Review hardware-impacting diffs separately from visual-only changes.

Changes that can actuate valves, gas sources or heat require tests, a documented fail-safe path, and supervised commissioning evidence. Never replace named commands with arbitrary output masks in the browser API.
