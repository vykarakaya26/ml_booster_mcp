"""Domain çekirdeği: varlıklar, değer nesneleri ve saf domain servisleri.

Bağımlılık kuralı: bu paket application/infrastructure/interfaces katmanlarını ve hiçbir
G/Ç kütüphanesini (httpx, duckdb, pyarrow, yaml, mcp, typer, pydantic) import etmez.
Zaman serisi hesapları için yalnızca pandas/numpy kullanılır (ADR: README → Mimari).
"""
