---
id: T-002
title: Add divide with a clear error
depends_on: [T-001]
spec: []
---

## Objective

Add `divide(a, b)` to `toy/calc.py`.

## Acceptance

- `divide(10, 4) == 2.5`
- `divide(1, 0)` raises `ValueError` with the message `cannot divide by zero`.

## Tests

Add tests to `tests/test_calc.py`, including the error case with `pytest.raises`.

## Notes
