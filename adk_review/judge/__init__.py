"""Applies the checks: every rule in `rules/` and every guideline in `guidelines/`, one file each and nothing else
there, and around them the catalog that finds them, the node that runs the rules, the agents a guideline becomes,
the tools, the plugins, the graph and the verifier.

May import `core` and `collect`; `rules/` imports `rule_helpers/` alone, `guidelines/` imports `core` alone, and
neither runs anything.
"""
