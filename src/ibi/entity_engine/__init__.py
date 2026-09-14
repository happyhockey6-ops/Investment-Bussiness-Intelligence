"""Entity resolution and identity boundary.

Owns the canonical identity of companies, people, and other tracked
entities — resolving "Apple", "Apple Inc.", and "AAPL" to one entity id
that every other engine can key off of. No resolution logic is implemented
in Phase 0.
"""
