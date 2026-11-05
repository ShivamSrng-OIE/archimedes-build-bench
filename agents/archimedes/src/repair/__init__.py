"""Turn evidence into one guarded repository edit, or none.

The package holds the hand-written repair rules (``deterministic``), the code that wraps
rule matches and model plans into patch candidates (``planner``), the portability and
competition-policy critics (``critic``, ``policy_critic``) and the all-or-nothing edit
executor with rollback (``executor``).
"""
