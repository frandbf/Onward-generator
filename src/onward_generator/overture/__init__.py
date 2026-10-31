"""Overture Maps places and buildings as third-party evidence (ADR-021).

``acquire`` is the network step (DuckDB over the release GeoParquet). ``match``
is offline: buildings become height overrides ranked below Catastro, places
become shop evidence that ``detail`` uses only where OpenStreetMap has none.
"""
