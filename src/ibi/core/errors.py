"""Shared exception types.

Kept small and semantic on purpose: engines should raise one of these (or a
subclass they define locally) rather than a bare ``Exception``, so logging
and callers can distinguish "your input was bad" from "an external
dependency failed" from "the platform's own invariants were violated".
"""

from __future__ import annotations


class IBIError(Exception):
    """Base class for all platform-specific exceptions."""


class ConfigurationError(IBIError):
    """Required configuration is missing or invalid."""


class ValidationError(IBIError):
    """Input failed a domain validation rule."""


class ProviderError(IBIError):
    """An external provider (AI or market data) failed or returned an
    unusable response. Should generally wrap the provider-specific
    exception rather than swallow it."""


class DeterminismViolation(IBIError):
    """Raised when code that must be deterministic (financial_engine
    calculations) detects a non-deterministic input or dependency, e.g. an
    attempt to call an AI provider from inside a calculation."""
