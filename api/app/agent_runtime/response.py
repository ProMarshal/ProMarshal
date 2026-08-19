"""Shared response delivery surface for Cortex and Cadence.

Both features import ResponsePayload and CortexResponseService from here
so neither has to reach into cortex/ internals directly.
"""

from app.cortex.response import CortexResponseService, ResponsePayload

__all__ = ["CortexResponseService", "ResponsePayload"]
