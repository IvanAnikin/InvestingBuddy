"""Azure Blob artifact store — V3.1 Slice 1.1.

The ONLY module in the corpus that is allowed to know Azure Blob Storage exists,
and it is reached exclusively through :class:`~app.services.corpus.artifacts.store.ArtifactStore`.

THE CONTAINER ALREADY EXISTS
============================
``infra/azure/modules/storage.bicep`` provisions ``investingbuddy-documents``
with ``allowBlobPublicAccess: false`` and TLS 1.2 minimum. This slice adds **no
infrastructure**: it uses the container that has been sitting empty since the
storage module was written.

WHY A CLIENT FACTORY INSTEAD OF AN SDK IMPORT
=============================================
The adapter takes a callable that returns a container client. Three consequences,
all of which matter more than the small indirection costs:

* the routine test suite exercises this adapter against a fake client and never
  needs ``azure-storage-blob`` installed, so CI stays offline and light;
* the SDK import happens inside :func:`azure_container_client_factory`, at first
  use, so importing the corpus package never drags in Azure;
* credentials are resolved in exactly one place, and never in domain code.

CREDENTIALS
===========
Managed identity (``DefaultAzureCredential``) against an account URL. No
connection string is accepted anywhere: a connection string *is* a key, and this
repository has already had one incident where a too-broad query briefly exposed
a real key. Nothing here logs a key, a blob name or a byte.

CONDITIONAL WRITE
=================
The upload passes ``overwrite=False``. If the blob already exists the service
rejects the write and the adapter reports a deduplication — the same bytes are
already there, because the blob name IS the content hash. That is a server-side
guarantee rather than a check-then-write race.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from app.services.corpus.artifacts.keys import artifact_key_for, is_valid_artifact_key
from app.services.corpus.artifacts.store import (
    BACKEND_AZURE_BLOB,
    DEFAULT_MAX_ARTIFACT_BYTES,
    ArtifactConflictError,
    ArtifactRef,
    ArtifactStoreError,
    ArtifactTooLargeError,
    PutResult,
    content_hash_of,
)

#: The container provisioned by ``infra/azure/modules/storage.bicep``.
DEFAULT_CONTAINER = "investingbuddy-documents"


class BlobContainerClientLike(Protocol):
    """The four operations this adapter needs from a container client.

    Structural, and deliberately tiny: it is the whole surface the corpus depends
    on, so a reader can see at a glance what would have to be reimplemented if the
    storage vendor changed.
    """

    async def upload_blob(self, name: str, data: bytes, **kwargs: Any) -> Any: ...

    async def download_blob(self, blob: str, **kwargs: Any) -> Any: ...

    async def get_blob_client(self, blob: str) -> Any: ...

    async def delete_blob(self, blob: str, **kwargs: Any) -> Any: ...


#: Produces a container client. Called once per operation and closed after, so a
#: credential is never held open across the process lifetime.
ContainerClientFactory = Callable[[], Any]


def _is_not_found(exc: BaseException) -> bool:
    """True for the SDK's "blob does not exist", identified without importing it.

    Matching on the exception's class name keeps this module import-free of the
    SDK. ``status_code == 404`` is checked first because it is the reliable
    signal; the name check covers the ``ResourceNotFoundError`` that arrives
    without one.
    """
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    return type(exc).__name__ in {"ResourceNotFoundError", "ResourceNotFound"}


def _is_already_exists(exc: BaseException) -> bool:
    """True for a conditional-write rejection because the blob is already there."""
    status = getattr(exc, "status_code", None)
    if status == 409:
        return True
    return type(exc).__name__ in {"ResourceExistsError", "ResourceExists"}


class AzureBlobArtifactStore:
    """Content-addressed artifacts in one Azure Blob container."""

    backend_name = BACKEND_AZURE_BLOB

    def __init__(
        self,
        *,
        client_factory: ContainerClientFactory,
        max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        self._client_factory = client_factory
        self._max_bytes = max(1, int(max_bytes))

    async def put(self, data: bytes, *, media_type: str) -> PutResult:
        if len(data) > self._max_bytes:
            raise ArtifactTooLargeError(
                f"artifact of {len(data)} bytes exceeds the {self._max_bytes}-byte ceiling"
            )
        digest = content_hash_of(data)
        key = artifact_key_for(digest, media_type=media_type)
        ref = ArtifactRef(
            content_hash=digest,
            storage_key=key,
            backend=self.backend_name,
            byte_size=len(data),
            media_type=media_type,
        )
        client = self._client_factory()
        try:
            try:
                # overwrite=False makes the "already stored" case a server-side
                # decision rather than a check-then-write race.
                await client.upload_blob(
                    name=key,
                    data=bytes(data),
                    overwrite=False,
                    content_type=media_type,
                )
            except Exception as exc:  # noqa: BLE001 - classified, never re-raised raw
                if not _is_already_exists(exc):
                    raise ArtifactStoreError(
                        f"artifact upload failed: {type(exc).__name__}"
                    ) from exc
                await self._assert_same_size(client, key, len(data))
                return PutResult(ref=ref, created=False, deduplicated=True)
        finally:
            await _close(client)
        return PutResult(ref=ref, created=True, deduplicated=False)

    @staticmethod
    async def _assert_same_size(client: Any, key: str, expected: int) -> None:
        """Fail closed when an existing blob's size disagrees with the new bytes.

        A same-hash, different-size blob means either a truncated earlier upload
        or a SHA-256 collision. Neither is something to resolve by guessing which
        copy is right, so it raises.
        """
        try:
            blob = await _maybe_await(client.get_blob_client(key))
            props = await _maybe_await(blob.get_blob_properties())
            size = getattr(props, "size", None)
        except Exception:  # noqa: BLE001 - the size probe is advisory
            return
        if size is not None and int(size) != expected:
            raise ArtifactConflictError(
                "stored artifact disagrees with the bytes being written"
            )

    async def get(self, key: str) -> bytes | None:
        if not is_valid_artifact_key(key):
            return None
        client = self._client_factory()
        try:
            stream = await _maybe_await(client.download_blob(key))
            return bytes(await _maybe_await(stream.readall()))
        except Exception as exc:  # noqa: BLE001 - a miss is normal, a failure is not
            if _is_not_found(exc):
                return None
            raise ArtifactStoreError(f"artifact read failed: {type(exc).__name__}") from exc
        finally:
            await _close(client)

    async def exists(self, key: str) -> bool:
        if not is_valid_artifact_key(key):
            return False
        client = self._client_factory()
        try:
            blob = await _maybe_await(client.get_blob_client(key))
            return bool(await _maybe_await(blob.exists()))
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return False
            raise ArtifactStoreError(f"artifact probe failed: {type(exc).__name__}") from exc
        finally:
            await _close(client)

    async def delete(self, key: str) -> bool:
        if not is_valid_artifact_key(key):
            return False
        client = self._client_factory()
        try:
            await _maybe_await(client.delete_blob(key))
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return False
            raise ArtifactStoreError(f"artifact delete failed: {type(exc).__name__}") from exc
        finally:
            await _close(client)


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable. Lets one adapter serve both client shapes."""
    if hasattr(value, "__await__"):
        return await value
    return value


async def _close(client: Any) -> None:
    closer = getattr(client, "close", None)
    if closer is None:
        return
    try:
        await _maybe_await(closer())
    except Exception:  # noqa: BLE001 - a close failure must not mask the result
        return


def azure_container_client_factory(
    *, account_url: str, container: str = DEFAULT_CONTAINER
) -> ContainerClientFactory:
    """A factory producing real Azure container clients via managed identity.

    The SDK is imported inside the returned callable, so nothing is imported until
    an Azure-backed store is actually used — the same lazy pattern the Document
    Intelligence OCR adapter follows. ``azure-storage-blob`` is an OPTIONAL extra
    (``pip install -e ".[blob]"``); CI does not install it and does not need to.
    """
    if not account_url:
        raise ArtifactStoreError("azure blob artifact store requires an account URL")

    def _factory() -> Any:
        try:
            from azure.identity.aio import DefaultAzureCredential
            from azure.storage.blob.aio import ContainerClient
        except ImportError as exc:  # pragma: no cover - depends on optional extras
            raise ArtifactStoreError(
                "azure blob artifact store requires the 'blob' extra "
                "(pip install -e '.[blob]')"
            ) from exc
        return ContainerClient(
            account_url=account_url,
            container_name=container,
            credential=DefaultAzureCredential(),
        )

    return _factory


__all__ = [
    "DEFAULT_CONTAINER",
    "AzureBlobArtifactStore",
    "BlobContainerClientLike",
    "ContainerClientFactory",
    "azure_container_client_factory",
]
