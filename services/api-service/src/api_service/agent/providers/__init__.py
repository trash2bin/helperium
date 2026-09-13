"""LLM provider transports for the narrow typed provider protocol.

Deliberately no eager re-exports here: importing a transport module (and its
transport library, e.g. litellm) must stay the caller's explicit decision.
Import ``base`` alone when only the contract is needed.
"""
