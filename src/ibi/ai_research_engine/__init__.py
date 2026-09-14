"""AI-assisted research boundary.

The only package that should hold prompt construction and retrieval logic
for AI-driven synthesis/classification/explanation, built on
`providers.ai`. Every output must be stored with an
`ibi.core.epistemics.EpistemicLabel` of INFERENCE, PREDICTION, or
SPECULATION — never FACT or CALCULATION. Not implemented in Phase 0.
"""
