"""EDU-IT FastAPI boundary (apps/api).

Thin HTTP boundary over the shared Python package. Validates with Pydantic v2.
Dependencies point inward (``api -> application -> domain <- infrastructure``).
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
