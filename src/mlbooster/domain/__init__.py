"""Domain core: entities, value objects and pure domain services.

Dependency rule: this package does not import the application/infrastructure/interfaces layers
or any I/O library (httpx, duckdb, pyarrow, yaml, mcp, typer, pydantic).
Only pandas/numpy are used for time-series computations (ADR: README → Architecture).
"""
