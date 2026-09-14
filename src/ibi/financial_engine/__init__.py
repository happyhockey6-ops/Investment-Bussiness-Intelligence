"""Deterministic financial computation.

Hard rule (see ARCHITECTURE.md): no AI model calculates a core financial
metric when deterministic source data and a formula are available. Every
function in this package must be a pure function of its typed inputs —
same inputs, same output, forever, with no call to an AI provider, no
network access, and no wall-clock dependency. `ibi.core.errors.DeterminismViolation`
exists for code that needs to assert this.

Phase 0 implements a small number of metrics end-to-end (with tests) to
prove the pattern — see `metrics.py` — rather than the full metric catalog
listed in ARCHITECTURE.md. Each additional metric should follow the same
shape: a typed input dataclass, a pure function, and a formula reference in
its docstring.
"""
