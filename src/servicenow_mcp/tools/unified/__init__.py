"""Unified tool surface (Phase 3a refactor).

Modules in this package expose a single entry point per concern - e.g. ``query``
covers what was previously split across ``table_query``, ``table_aggregate``, and
``record_get``. Old tools remain registered alongside until Phase 3b flips the
``PACKAGE_REGISTRY`` over.

The server loader (``server.py``) detects ``unified.*`` modules and injects the
``ChoiceRegistry`` exactly as it does for ``domain_*`` modules, so unified tools
can resolve display labels to underlying values.
"""
