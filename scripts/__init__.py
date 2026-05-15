"""Project runner scripts.

Marked as a package so internal helpers (e.g. `scripts._swebench_eval_results`)
can be shared across runners via plain `from scripts.foo import bar`, instead
of each runner mutating sys.path at import time.

Runners are still invoked directly as `python scripts/<runner>.py`; the
runners themselves are responsible for putting the repo root on sys.path
so the `scripts.*` import works.
"""
