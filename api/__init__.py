"""Backend package for the live portfolio engine.

This package provides the framework-agnostic core that the FastAPI service
(Phase 3) and the React frontend (Phase 4+) build on. Each submodule is
independently testable and contains no Streamlit or Plotly imports.

Modules:
    storage      Atomic, schema-versioned persistence with quarantine on parse failure.
    market_data  yfinance facade with quote/status types and a polling scheduler.
"""

__version__ = "0.1.0"
