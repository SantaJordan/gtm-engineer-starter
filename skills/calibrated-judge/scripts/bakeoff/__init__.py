"""calibrated-judge bake-off harness — judges vs. the keys already on disk.

Five decisions, one scorer, arms that all speak the same interface. Nothing in
this package ever reads a truth value except `sets.load_labels`, which the
scorer alone calls; the frozen evidence an arm sees is built by `freeze` and is
truth-free by construction (see `freeze.finalize`).
"""
