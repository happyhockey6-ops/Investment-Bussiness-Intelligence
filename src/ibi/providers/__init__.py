"""Vendor-independent provider abstractions.

Nothing outside this package (and its concrete implementations) should
import an AI SDK or a market-data client library directly. Domain engines
depend on the interfaces in `providers.ai.base` and
`providers.market_data.base`; which concrete provider is wired in is a
configuration decision (`ibi.config.Settings.ai_provider` /
`market_data_provider`), not a code decision.
"""
