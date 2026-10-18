---
id: T-003
title: Command line entry point
depends_on: [T-002]
spec: []
---

## Objective

Add `toy/__main__.py` so `python -m toy add 2 3` prints `5`.
It supports the operations `add`, `subtract`, `multiply`, `divide`.

## Acceptance

- `python -m toy add 2 3` prints `5` and exits 0.
- `python -m toy divide 1 0` prints `error: cannot divide by zero` to stderr and exits 1.
- An unknown operation prints usage and exits 2.

## Tests

Test `main(argv)` directly in `tests/test_cli.py` (no subprocess).

## Notes
