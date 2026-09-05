"""Entity master — legal-entity, security and listing identity (V3.2).

``identifiers`` holds the scheme semantics and checksums, ``vocabulary`` the closed
type sets, ``master`` the persistence. Nothing in this package performs I/O beyond
the database session it is handed: identifier *sources* (GLEIF, SEC, OpenFIGI) are
a network path and belong with slice 2.3.
"""
