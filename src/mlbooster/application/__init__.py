"""Application core: use cases and the ports (Protocols) facing the outside world.

Dependency rule: depends only on `mlbooster.domain`; infrastructure implements the ports and
the composition root (bootstrap.py) injects them.
"""
