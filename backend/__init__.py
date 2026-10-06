"""Complex-systems simulation platform backend.

Sub-packages / modules:

- :mod:`backend.catalog`       — domain/model metadata, defaults, interventions.
- :mod:`backend.storage`       — atomic, time-step-sharded JSON persistence.
- :mod:`backend.models`        — scene / experiment data models and validation.
- :mod:`backend.engine`        — simulation engines (CA + ABM per domain).
- :mod:`backend.run_manager`   — run lifecycle, real-time stepping, batch runs.
- :mod:`backend.report`        — per-run summary report generation.
- :mod:`backend.export`        — CSV / JSON data export.
- :mod:`backend.seed`          — example scenes.
- :mod:`backend.app`           — Flask application and REST API.
"""

__version__ = "1.0.0"
