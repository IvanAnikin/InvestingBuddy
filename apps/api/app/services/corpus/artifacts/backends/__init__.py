"""Concrete ``ArtifactStore`` implementations — V3.1 Slice 1.1.

``InMemoryArtifactStore``
    Unit tests. No filesystem, no network.
``LocalFilesystemArtifactStore``
    Local development, and the opt-in local acceptance run.
``AzureBlobArtifactStore``
    Deployed environments, once V3 is approved.

The Azure adapter is the only module in the corpus that may know Azure exists,
and even it takes an injected client factory so the SDK import is lazy and the
routine test suite never needs the package installed.
"""
