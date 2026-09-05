"""Provider runtime — interfaces, governance, routing and fakes (V3.4).

``contracts`` defines the four interfaces and the canonical result contract,
``governance`` decides which provider may see which class of content, ``routing`` maps
slots to providers, and ``fakes`` are the only clients the unit suite touches.

No vendor SDK is imported anywhere in this package, and no live adapter exists —
`OPEN DECISIONS #3, #4, #6 and #7 <../../../../docs/v3/OPEN_DECISIONS.md>`_ are
user-owned, and tests fail if an adapter appears before one is taken.
"""
