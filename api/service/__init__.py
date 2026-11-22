"""FastAPI service layer for the live portfolio engine.

The service composes the pure modules (``api.storage``, ``api.market_data``,
``api.portfolio``, ``api.strategies``) into HTTP and WebSocket surfaces. The
React frontend is the primary client; nothing in this layer is Streamlit-
aware.
"""
