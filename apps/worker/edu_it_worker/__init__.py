"""EDU-IT task-queue consumer (apps/worker).

Thin boundary over the shared Python package. Dependencies point inward
(``worker -> application -> domain <- infrastructure``).
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
