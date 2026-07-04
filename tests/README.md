# Tests

Self-contained — each spins up a throwaway FAKE-mode DB in a temp dir, no env setup needed:

```sh
python3 tests/test_e2e.py      # full loop end-to-end: triage → coding PR lifecycle → gate → done
python3 tests/test_safety.py   # tick-lock, lease atomicity, green-CI merge gate, stall guard
```

Both exit non-zero on failure (plain `assert`s) and print a `PASSED` line on success.
They run against FAKE agents + a simulated git/gh, so no `claude`/`git`/`gh` is required.
