"""Alembic migration environment for the EDU-IT data model (Req 4).

The package holds the Alembic ``env.py``, the ``script.py.mako`` template, and
the ``versions/`` revision scripts. The
:class:`~edu_it_infrastructure.persistence.runner.Migration_Runner` drives this
environment programmatically; ``alembic.ini`` here points Alembic at this
directory and at the ``versions`` package.
"""

from __future__ import annotations
