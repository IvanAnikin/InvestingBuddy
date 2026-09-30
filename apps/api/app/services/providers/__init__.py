"""Provider runtime — interfaces, governance, routing and fakes (V3.4).

``contracts`` defines the four interfaces and the canonical result contract,
``governance`` decides which provider may see which class of content, ``routing`` maps
slots to providers, and ``fakes`` are the only clients the unit suite touches.

No vendor SDK is imported anywhere in this package and nothing in it opens a socket
(``tests/test_v3_provider_interfaces.py`` enforces that). Live adapters DO exist, outside
it: the DeepSeek model, search and research providers live in
``app/integrations/deepseek/`` and reach this package only through its interfaces
(ADR-048/049 took the decisions they needed).
"""
