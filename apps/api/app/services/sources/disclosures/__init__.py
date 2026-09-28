"""Non-US primary disclosures — UK (FCA National Storage Mechanism) and ASX.

The chain this package exists for::

    verified issuer → official announcement → the actual document → persisted artifact
    → extraction → corpus chunks → period / scope / provenance → V3 question retrieval

Discovery is METADATA; only the fetched document's own text is evidence. See
``docs/non-us-primary-documents.md``.
"""
