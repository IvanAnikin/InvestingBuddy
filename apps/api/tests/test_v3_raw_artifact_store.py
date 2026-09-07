"""The raw artifact store — V3.1 Slice 1.1.

WHAT THESE TESTS PIN
====================
V3.1's phase demonstration (ACCEPTANCE_AND_TEST_STRATEGY §3) starts with a real
document surviving ingestion. This file covers the bottom layer of that: the
bytes themselves.

  * content hash IS the identity — the key is a pure function of the bytes;
  * storing the same document twice deduplicates instead of duplicating;
  * an artifact is never overwritten with different bytes;
  * a storage failure degrades to an honest lineage record, never to a crash;
  * policy decides whether bytes may be kept at all, and lineage survives when
    they may not;
  * ``blob_path`` — NULL on every row ever written since migration 013 — becomes
    a real retrieval path;
  * with ``V3_CORPUS_ENABLED`` off, nothing is stored, nothing is queried, and
    the V2 path is unchanged.

The backends are exercised as REAL implementations, not mocks. A mocked store
would let every assertion here pass while the contract was wrong, because the
assertion would be about what we asked the mock.

No clock dependence: every expiry test passes an explicit ``now``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_artifact as _research_artifact  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.research_artifact import ResearchArtifact
from app.services.corpus.artifacts.backends.local_fs import LocalFilesystemArtifactStore
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
from app.services.corpus.artifacts.keys import (
    ARTIFACT_KEY_RE,
    artifact_key_for,
    extension_for_media_type,
    hash_from_artifact_key,
    is_valid_artifact_key,
)
from app.services.corpus.artifacts.service import (
    expire_artifacts,
    load_artifact_bytes,
    record_artifact,
    resolve_artifact_store,
    store_raw_artifact,
)
from app.services.corpus.artifacts.store import (
    BACKEND_AZURE_BLOB,
    BACKEND_LOCAL,
    BACKEND_MEMORY,
    BACKEND_NONE,
    FAILURE_CONFLICT,
    FAILURE_POLICY_DENIED,
    FAILURE_STORE_ERROR,
    FAILURE_STORE_UNAVAILABLE,
    FAILURE_TOO_LARGE,
    ArtifactConflictError,
    ArtifactStore,
    ArtifactStoreError,
    ArtifactTooLargeError,
    content_hash_of,
)
from app.services.corpus.policy import (
    ACCESS_DERIVED,
    ACCESS_LICENSED_PRIVATE,
    ACCESS_PUBLIC_ISSUER,
    ACCESS_PUBLIC_OFFICIAL,
    ACCESS_PUBLIC_WEB,
    ACCESS_USER_PRIVATE,
    QUOTE_BOUNDED,
    QUOTE_FULL,
    QUOTE_NONE,
    default_policy_for,
    derive_policy,
    normalize_access_class,
)

PDF_BYTES = b"%PDF-1.7\nPandora annual report 2025 (fixture)\n%%EOF"
OTHER_PDF_BYTES = b"%PDF-1.7\nCompagnie Financiere Richemont FY2025 (fixture)\n%%EOF"


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "v3_corpus_enabled": True,
        "v3_artifact_store_backend": BACKEND_MEMORY,
        "v3_artifact_retention_days": 0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Key scheme
# --------------------------------------------------------------------------- #


class TestKeyScheme:
    def test_the_key_is_a_pure_function_of_the_bytes(self) -> None:
        digest = content_hash_of(PDF_BYTES)
        assert artifact_key_for(digest, media_type="application/pdf") == artifact_key_for(
            digest, media_type="application/pdf"
        )

    def test_the_key_matches_the_declared_shape(self) -> None:
        key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
        assert ARTIFACT_KEY_RE.match(key)
        assert key.startswith("sha256/")
        assert key.endswith(".pdf")

    def test_the_key_fans_out_on_the_hash_prefix(self) -> None:
        digest = content_hash_of(PDF_BYTES)
        key = artifact_key_for(digest, media_type="application/pdf")
        assert key.split("/")[1] == digest[0:2]
        assert key.split("/")[2] == digest[2:4]

    def test_the_hash_is_recoverable_from_the_key(self) -> None:
        digest = content_hash_of(PDF_BYTES)
        key = artifact_key_for(digest, media_type="text/html")
        assert hash_from_artifact_key(key) == digest

    def test_no_url_ticker_or_issuer_can_reach_a_key(self) -> None:
        # The key is hex + a closed extension. Nothing issuer-identifying, and no
        # credential-bearing URL fragment, can appear in a storage listing or log.
        key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
        assert not any(ch.isupper() for ch in key)
        for leak in ("pandora", "pndora", "http", "?", "&", "token", "="):
            assert leak not in key

    def test_an_unknown_media_type_cannot_choose_the_extension(self) -> None:
        # A hostile Content-Type must not decide a filename suffix on disk.
        assert extension_for_media_type("application/x-msdownload") == "bin"
        assert extension_for_media_type("text/html; charset=utf-8") == "html"
        assert extension_for_media_type(None) == "bin"

    def test_a_non_sha256_identity_is_refused_rather_than_coerced(self) -> None:
        for bad in ("", "nothex", "abc", "0" * 63, "0" * 65, "g" * 64):
            with pytest.raises(ValueError):
                artifact_key_for(bad, media_type="application/pdf")

    def test_an_uppercase_digest_normalises_to_the_same_key(self) -> None:
        digest = content_hash_of(PDF_BYTES)
        assert artifact_key_for(digest.upper(), media_type="application/pdf") == (
            artifact_key_for(digest, media_type="application/pdf")
        )

    def test_a_traversal_shaped_key_is_not_one_of_ours(self) -> None:
        assert not is_valid_artifact_key("sha256/../../etc/passwd")
        assert not is_valid_artifact_key("/etc/passwd")
        assert not is_valid_artifact_key("sha256/aa/bb/" + "0" * 64 + ".pdf/../x")


# --------------------------------------------------------------------------- #
# Backend contract — run against every real backend
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["memory", "local"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ArtifactStore:
    if request.param == "memory":
        return InMemoryArtifactStore()
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


class TestBackendContract:
    async def test_put_then_get_returns_the_exact_bytes(self, store: ArtifactStore) -> None:
        put = await store.put(PDF_BYTES, media_type="application/pdf")
        assert put.created is True
        assert put.deduplicated is False
        assert await store.get(put.ref.storage_key) == PDF_BYTES

    async def test_the_same_document_twice_deduplicates(self, store: ArtifactStore) -> None:
        first = await store.put(PDF_BYTES, media_type="application/pdf")
        second = await store.put(PDF_BYTES, media_type="application/pdf")
        assert first.ref.storage_key == second.ref.storage_key
        assert second.created is False
        assert second.deduplicated is True

    async def test_exactly_one_of_created_and_deduplicated_is_true(
        self, store: ArtifactStore
    ) -> None:
        for _ in range(3):
            result = await store.put(PDF_BYTES, media_type="application/pdf")
            assert result.created != result.deduplicated

    async def test_different_documents_get_different_keys(self, store: ArtifactStore) -> None:
        a = await store.put(PDF_BYTES, media_type="application/pdf")
        b = await store.put(OTHER_PDF_BYTES, media_type="application/pdf")
        assert a.ref.storage_key != b.ref.storage_key
        assert await store.get(a.ref.storage_key) == PDF_BYTES
        assert await store.get(b.ref.storage_key) == OTHER_PDF_BYTES

    async def test_a_missing_key_is_none_not_an_exception(self, store: ArtifactStore) -> None:
        missing = artifact_key_for("f" * 64, media_type="application/pdf")
        assert await store.get(missing) is None
        assert await store.exists(missing) is False

    async def test_delete_is_idempotent(self, store: ArtifactStore) -> None:
        put = await store.put(PDF_BYTES, media_type="application/pdf")
        assert await store.delete(put.ref.storage_key) is True
        assert await store.delete(put.ref.storage_key) is False
        assert await store.get(put.ref.storage_key) is None

    async def test_a_foreign_key_is_never_read_written_or_deleted(
        self, store: ArtifactStore
    ) -> None:
        for hostile in ("../../etc/passwd", "sha256/x", "", "/absolute"):
            assert await store.get(hostile) is None
            assert await store.exists(hostile) is False
            assert await store.delete(hostile) is False

    async def test_the_size_ceiling_is_enforced_before_anything_is_written(
        self, tmp_path: Path
    ) -> None:
        for tiny in (InMemoryArtifactStore(max_bytes=8), LocalFilesystemArtifactStore(tmp_path, max_bytes=8)):
            with pytest.raises(ArtifactTooLargeError):
                await tiny.put(PDF_BYTES, media_type="application/pdf")
            key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
            assert await tiny.get(key) is None

    async def test_concurrent_puts_of_the_same_document_produce_one_artifact(
        self, store: ArtifactStore
    ) -> None:
        results = await asyncio.gather(
            *(store.put(PDF_BYTES, media_type="application/pdf") for _ in range(8))
        )
        assert len({r.ref.storage_key for r in results}) == 1
        assert sum(1 for r in results if r.created) == 1
        assert sum(1 for r in results if r.deduplicated) == 7

    async def test_every_backend_satisfies_the_protocol(self, store: ArtifactStore) -> None:
        assert isinstance(store, ArtifactStore)


class TestLocalFilesystemSpecifics:
    async def test_bytes_land_inside_the_configured_root(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        store = LocalFilesystemArtifactStore(root)
        put = await store.put(PDF_BYTES, media_type="application/pdf")
        written = list(root.rglob("*.pdf"))
        assert len(written) == 1
        assert written[0].read_bytes() == PDF_BYTES
        assert str(written[0]).startswith(str(root.resolve()))
        assert put.ref.storage_key in str(written[0]).replace("\\", "/")

    async def test_no_partial_file_survives_a_completed_write(self, tmp_path: Path) -> None:
        store = LocalFilesystemArtifactStore(tmp_path)
        await store.put(PDF_BYTES, media_type="application/pdf")
        assert list(tmp_path.rglob("*.part")) == []

    async def test_a_truncated_prior_write_is_a_conflict_not_an_overwrite(
        self, tmp_path: Path
    ) -> None:
        # The exact failure this repository has already paid for once: a truncated
        # artifact treated as authoritative. Different length under the same digest
        # fails closed rather than silently picking one.
        store = LocalFilesystemArtifactStore(tmp_path)
        key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PDF_BYTES[:5])
        with pytest.raises(ArtifactConflictError):
            await store.put(PDF_BYTES, media_type="application/pdf")
        assert path.read_bytes() == PDF_BYTES[:5]

    async def test_an_existing_identical_artifact_is_not_rewritten(
        self, tmp_path: Path
    ) -> None:
        store = LocalFilesystemArtifactStore(tmp_path)
        first = await store.put(PDF_BYTES, media_type="application/pdf")
        path = tmp_path / first.ref.storage_key
        before = path.stat().st_mtime_ns
        second = await store.put(PDF_BYTES, media_type="application/pdf")
        assert second.deduplicated is True
        assert path.stat().st_mtime_ns == before


class TestMemoryStoreConflict:
    async def test_a_disagreeing_stored_object_fails_closed(self) -> None:
        store = InMemoryArtifactStore()
        key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
        store._objects[key] = PDF_BYTES[:4]  # simulate a truncated earlier write
        with pytest.raises(ArtifactConflictError):
            await store.put(PDF_BYTES, media_type="application/pdf")


# --------------------------------------------------------------------------- #
# Azure adapter — against a fake client, so CI never needs the SDK
# --------------------------------------------------------------------------- #


class _FakeAzureError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class _FakeBlob:
    def __init__(self, container: "_FakeContainerClient", name: str) -> None:
        self._container = container
        self._name = name

    async def exists(self) -> bool:
        return self._name in self._container.blobs

    async def get_blob_properties(self):  # noqa: ANN201 - a stand-in for the SDK shape
        if self._name not in self._container.blobs:
            raise _FakeAzureError(404)

        class _Props:
            size = len(self._container.blobs[self._name])

        return _Props()


class _FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def readall(self) -> bytes:
        return self._data


class _FakeContainerClient:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.closed = False
        self.uploads: list[tuple[str, bool, str]] = []

    async def upload_blob(self, name: str, data: bytes, **kwargs: object) -> None:
        self.uploads.append((name, bool(kwargs.get("overwrite")), str(kwargs.get("content_type"))))
        if name in self.blobs and not kwargs.get("overwrite"):
            raise _FakeAzureError(409)
        self.blobs[name] = bytes(data)

    async def download_blob(self, blob: str, **kwargs: object) -> _FakeDownload:
        if blob not in self.blobs:
            raise _FakeAzureError(404)
        return _FakeDownload(self.blobs[blob])

    def get_blob_client(self, blob: str) -> _FakeBlob:
        return _FakeBlob(self, blob)

    async def delete_blob(self, blob: str, **kwargs: object) -> None:
        if blob not in self.blobs:
            raise _FakeAzureError(404)
        del self.blobs[blob]

    async def close(self) -> None:
        self.closed = True


class TestAzureBlobAdapter:
    def _store(self, blobs: dict[str, bytes] | None = None):
        from app.services.corpus.artifacts.backends.azure_blob import AzureBlobArtifactStore

        shared = blobs if blobs is not None else {}
        clients: list[_FakeContainerClient] = []

        def _factory() -> _FakeContainerClient:
            client = _FakeContainerClient(shared)
            clients.append(client)
            return client

        return AzureBlobArtifactStore(client_factory=_factory), shared, clients

    async def test_put_get_delete_round_trip(self) -> None:
        store, blobs, _ = self._store()
        put = await store.put(PDF_BYTES, media_type="application/pdf")
        assert put.created is True
        assert blobs[put.ref.storage_key] == PDF_BYTES
        assert await store.get(put.ref.storage_key) == PDF_BYTES
        assert await store.exists(put.ref.storage_key) is True
        assert await store.delete(put.ref.storage_key) is True
        assert await store.get(put.ref.storage_key) is None

    async def test_the_write_is_conditional_never_an_overwrite(self) -> None:
        store, _, clients = self._store()
        await store.put(PDF_BYTES, media_type="application/pdf")
        second = await store.put(PDF_BYTES, media_type="application/pdf")
        assert second.deduplicated is True
        # overwrite=False on every upload: "already stored" is the service's
        # decision, not a check-then-write race in our code.
        assert all(overwrite is False for _, overwrite, _ in
                   [u for c in clients for u in c.uploads])

    async def test_a_disagreeing_existing_blob_fails_closed(self) -> None:
        key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
        store, _, _ = self._store({key: PDF_BYTES[:3]})
        from app.services.corpus.artifacts.store import ArtifactConflictError as Conflict

        with pytest.raises(Conflict):
            await store.put(PDF_BYTES, media_type="application/pdf")

    async def test_a_missing_blob_reads_as_none(self) -> None:
        store, _, _ = self._store()
        assert await store.get(artifact_key_for("a" * 64, media_type="application/pdf")) is None

    async def test_every_client_is_closed(self) -> None:
        store, _, clients = self._store()
        await store.put(PDF_BYTES, media_type="application/pdf")
        await store.get(artifact_key_for("b" * 64, media_type="application/pdf"))
        assert clients and all(c.closed for c in clients)

    async def test_a_real_failure_is_surfaced_not_swallowed(self) -> None:
        from app.services.corpus.artifacts.backends.azure_blob import AzureBlobArtifactStore

        class _Broken(_FakeContainerClient):
            async def upload_blob(self, name: str, data: bytes, **kwargs: object) -> None:
                raise _FakeAzureError(503)

        store = AzureBlobArtifactStore(client_factory=lambda: _Broken({}))
        with pytest.raises(ArtifactStoreError):
            await store.put(PDF_BYTES, media_type="application/pdf")

    def test_the_adapter_never_imports_the_sdk_at_module_scope(self) -> None:
        # The corpus must be importable without azure-storage-blob installed. If
        # this ever regresses, CI stops being offline-safe.
        import ast

        source = Path("app/services/corpus/artifacts/backends/azure_blob.py").read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [getattr(node, "module", "") or ""] + [a.name for a in node.names]
                assert not any(n.startswith("azure") for n in names)

    def test_no_domain_module_imports_an_azure_sdk(self) -> None:
        # The load-bearing rule from ARCHITECTURE_SPEC §3.1: the research domain
        # imports provider INTERFACES, never provider SDKs. Exactly one file in the
        # whole corpus package is allowed to reference the vendor, and even it does
        # so lazily inside a function.
        import ast

        offenders: list[str] = []
        for path in sorted(Path("app/services/corpus").rglob("*.py")):
            if path.name == "azure_blob.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(n.split(".")[0] == "azure" for n in names):
                    offenders.append(str(path))
        assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


class TestPolicy:
    def test_public_classes_get_the_governance_defaults(self) -> None:
        for access in (ACCESS_PUBLIC_OFFICIAL, ACCESS_PUBLIC_ISSUER):
            policy = default_policy_for(access)
            assert policy.stored and policy.indexed and policy.sent_to_external_model
            assert policy.quoted == QUOTE_FULL
            assert policy.retained_long_term

    def test_public_web_is_quotable_only_in_bounded_form(self) -> None:
        assert default_policy_for(ACCESS_PUBLIC_WEB).quoted == QUOTE_BOUNDED

    def test_user_private_is_never_sent_to_an_external_model_by_default(self) -> None:
        policy = default_policy_for(ACCESS_USER_PRIVATE)
        assert policy.sent_to_external_model is False
        assert policy.stored is True

    def test_licensed_private_denies_by_default(self) -> None:
        policy = default_policy_for(ACCESS_LICENSED_PRIVATE)
        assert not policy.stored
        assert not policy.indexed
        assert not policy.sent_to_external_model
        assert policy.quoted == QUOTE_NONE
        assert not policy.may_store_bytes

    def test_an_unknown_class_is_treated_as_the_most_restrictive(self) -> None:
        assert normalize_access_class("something_new") == ACCESS_LICENSED_PRIVATE
        assert normalize_access_class(None) == ACCESS_LICENSED_PRIVATE
        assert default_policy_for("something_new").may_store_bytes is False

    def test_derived_inherits_the_most_restrictive_input(self) -> None:
        derived = derive_policy(
            [default_policy_for(ACCESS_PUBLIC_OFFICIAL), default_policy_for(ACCESS_USER_PRIVATE)]
        )
        assert derived.access_class == ACCESS_DERIVED
        assert derived.sent_to_external_model is False
        assert derived.quoted == QUOTE_FULL

    def test_derived_from_a_bounded_input_is_bounded(self) -> None:
        derived = derive_policy(
            [default_policy_for(ACCESS_PUBLIC_WEB), default_policy_for(ACCESS_PUBLIC_ISSUER)]
        )
        assert derived.quoted == QUOTE_BOUNDED

    def test_derived_from_nothing_denies(self) -> None:
        assert derive_policy([]).may_store_bytes is False

    def test_a_derivation_never_outlives_its_shortest_lived_input(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        short = default_policy_for(ACCESS_PUBLIC_WEB, retention_days=7, now=now)
        long = default_policy_for(ACCESS_PUBLIC_ISSUER, retention_days=400, now=now)
        assert derive_policy([short, long]).retention_expires_at == short.retention_expires_at

    def test_no_ttl_configured_is_not_expired(self) -> None:
        policy = default_policy_for(ACCESS_PUBLIC_ISSUER, retention_days=0)
        assert policy.retention_expires_at is None
        assert policy.is_expired(datetime(2099, 1, 1, tzinfo=timezone.utc)) is False

    def test_a_configured_ttl_expires_exactly_when_stated(self) -> None:
        now = datetime(2026, 9, 4, tzinfo=timezone.utc)
        policy = default_policy_for(ACCESS_PUBLIC_ISSUER, retention_days=30, now=now)
        assert policy.retention_expires_at == now + timedelta(days=30)
        assert policy.is_expired(now + timedelta(days=29)) is False
        assert policy.is_expired(now + timedelta(days=30)) is True


# --------------------------------------------------------------------------- #
# Store resolution from configuration
# --------------------------------------------------------------------------- #


class TestStoreResolution:
    def test_the_corpus_flag_gates_everything(self) -> None:
        assert resolve_artifact_store(_cfg(v3_corpus_enabled=False)) is None

    def test_none_is_the_default_backend(self) -> None:
        cfg = Settings(v3_corpus_enabled=True)
        assert cfg.v3_artifact_store_backend == BACKEND_NONE
        assert resolve_artifact_store(cfg) is None

    def test_local_requires_a_root_and_never_raises_without_one(self) -> None:
        assert resolve_artifact_store(_cfg(v3_artifact_store_backend=BACKEND_LOCAL)) is None

    def test_local_resolves_with_a_root(self, tmp_path: Path) -> None:
        store = resolve_artifact_store(
            _cfg(
                v3_artifact_store_backend=BACKEND_LOCAL,
                v3_artifact_store_local_root=str(tmp_path),
            )
        )
        assert isinstance(store, LocalFilesystemArtifactStore)

    def test_azure_requires_an_account_url_and_never_raises_without_one(self) -> None:
        assert resolve_artifact_store(_cfg(v3_artifact_store_backend=BACKEND_AZURE_BLOB)) is None

    def test_an_unrecognised_backend_stores_nothing_rather_than_guessing(self) -> None:
        assert resolve_artifact_store(_cfg(v3_artifact_store_backend="s3")) is None

    def test_a_connection_string_setting_does_not_exist(self) -> None:
        # A connection string IS a key. There is deliberately nowhere to put one.
        fields = set(Settings.model_fields)
        assert not any("connection_string" in f for f in fields)


# --------------------------------------------------------------------------- #
# store_raw_artifact — the retention decision
# --------------------------------------------------------------------------- #


class TestStoreRawArtifact:
    async def test_the_corpus_flag_off_does_nothing_at_all(self) -> None:
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(v3_corpus_enabled=False),
            store=InMemoryArtifactStore(),
        )
        assert result is None

    async def test_a_public_issuer_document_is_retained(self) -> None:
        store = InMemoryArtifactStore()
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=store,
        )
        assert result is not None
        assert result.bytes_retained
        assert result.created is True
        assert result.failure_code is None
        assert await store.get(result.storage_key or "") == PDF_BYTES

    async def test_the_same_document_twice_is_recorded_as_a_deduplication(self) -> None:
        store = InMemoryArtifactStore()
        kwargs = dict(
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=store,
        )
        first = await store_raw_artifact(PDF_BYTES, **kwargs)  # type: ignore[arg-type]
        second = await store_raw_artifact(PDF_BYTES, **kwargs)  # type: ignore[arg-type]
        assert first is not None and second is not None
        assert second.deduplicated is True and second.created is False
        assert first.storage_key == second.storage_key

    async def test_a_licensed_document_keeps_lineage_and_loses_bytes(self) -> None:
        store = InMemoryArtifactStore()
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_LICENSED_PRIVATE,
            cfg=_cfg(),
            store=store,
        )
        assert result is not None
        assert result.bytes_retained is False
        assert result.backend == BACKEND_NONE
        assert result.failure_code == FAILURE_POLICY_DENIED
        # The hash and the policy survive; only the bytes do not.
        assert result.content_hash == content_hash_of(PDF_BYTES)
        assert await store.get(artifact_key_for(result.content_hash, media_type="application/pdf")) is None

    async def test_no_configured_store_is_an_honest_record_not_a_crash(self) -> None:
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(v3_artifact_store_backend=BACKEND_NONE),
        )
        assert result is not None
        assert result.failure_code == FAILURE_STORE_UNAVAILABLE
        assert result.bytes_retained is False

    async def test_a_store_failure_degrades_rather_than_raising(self) -> None:
        class _Exploding:
            backend_name = "memory"

            async def put(self, data: bytes, *, media_type: str):  # noqa: ANN201
                raise ArtifactStoreError("boom")

            async def get(self, key: str) -> bytes | None:
                return None

            async def exists(self, key: str) -> bool:
                return False

            async def delete(self, key: str) -> bool:
                return False

        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=_Exploding(),  # type: ignore[arg-type]
        )
        assert result is not None
        assert result.failure_code == FAILURE_STORE_ERROR
        assert result.bytes_retained is False

    async def test_an_oversized_artifact_is_refused_with_a_named_reason(self) -> None:
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=InMemoryArtifactStore(max_bytes=4),
        )
        assert result is not None
        assert result.failure_code == FAILURE_TOO_LARGE

    async def test_a_conflict_is_refused_with_a_named_reason(self) -> None:
        store = InMemoryArtifactStore()
        key = artifact_key_for(content_hash_of(PDF_BYTES), media_type="application/pdf")
        store._objects[key] = PDF_BYTES[:2]
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=store,
        )
        assert result is not None
        assert result.failure_code == FAILURE_CONFLICT

    async def test_a_configured_ttl_is_stamped_on_the_record(self) -> None:
        now = datetime(2026, 9, 4, tzinfo=timezone.utc)
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(v3_artifact_retention_days=90),
            store=InMemoryArtifactStore(),
            now=now,
        )
        assert result is not None
        assert result.retention_expires_at == now + timedelta(days=90)

    async def test_the_default_configuration_records_no_ttl(self) -> None:
        result = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=InMemoryArtifactStore(),
        )
        assert result is not None
        assert result.retention_expires_at is None


# --------------------------------------------------------------------------- #
# Lineage persistence against a real database
# --------------------------------------------------------------------------- #


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


class TestLineagePersistence:
    async def test_one_row_per_distinct_document(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        for _ in range(3):
            stored = await store_raw_artifact(
                PDF_BYTES,
                media_type="application/pdf",
                access_class=ACCESS_PUBLIC_ISSUER,
                cfg=cfg,
                store=store,
            )
            await record_artifact(session, stored, cfg=cfg)
        rows = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert len(rows) == 1
        assert rows[0].seen_count == 3

    async def test_the_row_carries_the_policy_as_queryable_columns(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_WEB,
            cfg=cfg,
            store=InMemoryArtifactStore(),
        )
        row = await record_artifact(session, stored, cfg=cfg)
        assert row is not None
        assert row.access_class == ACCESS_PUBLIC_WEB
        assert row.policy_indexed is True
        assert row.policy_quoted == QUOTE_BOUNDED
        assert row.storage_key is not None
        assert row.stored_at is not None
        assert row.bytes_deleted_at is None

    async def test_a_policy_refused_document_still_gets_lineage(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_LICENSED_PRIVATE,
            cfg=cfg,
            store=InMemoryArtifactStore(),
        )
        row = await record_artifact(session, stored, cfg=cfg)
        assert row is not None
        assert row.storage_key is None
        assert row.storage_backend == BACKEND_NONE
        assert row.stored_at is None
        # NULL storage_key is never ambiguous: the backend column says why.

    async def test_bytes_can_be_added_to_an_existing_byteless_row(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        denied = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(v3_artifact_store_backend=BACKEND_NONE),
        )
        await record_artifact(session, denied, cfg=cfg)
        allowed = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=cfg,
            store=InMemoryArtifactStore(),
        )
        row = await record_artifact(session, allowed, cfg=cfg)
        assert row is not None
        assert row.storage_key is not None
        assert row.seen_count == 2

    async def test_the_corpus_flag_off_writes_no_row(self, session) -> None:  # noqa: ANN001
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=_cfg(),
            store=InMemoryArtifactStore(),
        )
        assert await record_artifact(session, stored, cfg=_cfg(v3_corpus_enabled=False)) is None
        assert (await session.execute(select(ResearchArtifact))).scalars().all() == []

    async def test_retrieval_by_content_hash_returns_the_original_bytes(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=cfg,
            store=store,
        )
        await record_artifact(session, stored, cfg=cfg)
        got = await load_artifact_bytes(
            session, content_hash=content_hash_of(PDF_BYTES), cfg=cfg, store=store
        )
        assert got == PDF_BYTES

    async def test_retrieval_of_an_unretained_document_is_none(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_LICENSED_PRIVATE,
            cfg=cfg,
            store=InMemoryArtifactStore(),
        )
        await record_artifact(session, stored, cfg=cfg)
        assert (
            await load_artifact_bytes(
                session, content_hash=content_hash_of(PDF_BYTES), cfg=cfg
            )
            is None
        )

    async def test_a_store_returning_the_wrong_object_is_refused(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=cfg,
            store=store,
        )
        await record_artifact(session, stored, cfg=cfg)
        # Corrupt the stored object underneath the key. Trusting the key would
        # attach this document's citation to different text.
        store._objects[stored.storage_key or ""] = OTHER_PDF_BYTES
        assert (
            await load_artifact_bytes(
                session, content_hash=content_hash_of(PDF_BYTES), cfg=cfg, store=store
            )
            is None
        )

    async def test_two_companies_documents_never_share_an_artifact_row_wrongly(
        self, session
    ) -> None:  # noqa: ANN001
        # An artifact is bytes. Two DIFFERENT documents are two rows; the same
        # bytes fetched twice are one row, whoever fetched them.
        cfg = _cfg()
        store = InMemoryArtifactStore()
        for payload in (PDF_BYTES, OTHER_PDF_BYTES):
            stored = await store_raw_artifact(
                payload,
                media_type="application/pdf",
                access_class=ACCESS_PUBLIC_ISSUER,
                cfg=cfg,
                store=store,
            )
            await record_artifact(session, stored, cfg=cfg)
        rows = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert len(rows) == 2
        assert {r.content_hash for r in rows} == {
            content_hash_of(PDF_BYTES),
            content_hash_of(OTHER_PDF_BYTES),
        }


# --------------------------------------------------------------------------- #
# Retention sweep — the primitive, never a schedule
# --------------------------------------------------------------------------- #


class TestRetention:
    async def _seed(self, session, cfg, *, retention_days: int, now: datetime):  # noqa: ANN001, ANN202
        store = InMemoryArtifactStore()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=Settings(
                v3_corpus_enabled=True,
                v3_artifact_store_backend=BACKEND_MEMORY,
                v3_artifact_retention_days=retention_days,
            ),
            store=store,
            now=now,
        )
        await record_artifact(session, stored, cfg=cfg, now=now)
        return store

    async def test_no_ttl_means_never_a_candidate(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        await self._seed(session, cfg, retention_days=0, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        report = await expire_artifacts(
            session, cfg=cfg, now=datetime(2099, 1, 1, tzinfo=timezone.utc), dry_run=False
        )
        assert report.candidates == 0
        assert report.deleted == 0
        rows = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert rows[0].storage_key is not None

    async def test_dry_run_is_the_default_and_touches_nothing(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await self._seed(session, cfg, retention_days=30, now=now)
        report = await expire_artifacts(session, cfg=cfg, now=now + timedelta(days=31))
        assert report.dry_run is True
        assert report.candidates == 1
        assert report.deleted == 0
        rows = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert rows[0].storage_key is not None

    async def test_an_expired_artifact_loses_bytes_and_keeps_lineage(self, session) -> None:  # noqa: ANN001
        cfg = Settings(
            v3_corpus_enabled=True,
            v3_artifact_store_backend=BACKEND_MEMORY,
            v3_artifact_retention_days=30,
        )
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await self._seed(session, cfg, retention_days=30, now=now)
        report = await expire_artifacts(
            session, cfg=cfg, now=now + timedelta(days=31), dry_run=False
        )
        assert report.deleted == 1
        row = (await session.execute(select(ResearchArtifact))).scalars().one()
        assert row.storage_key is None
        assert row.storage_backend == BACKEND_NONE
        assert row.bytes_deleted_at is not None
        # The lineage row itself is never deleted — the citation still resolves.
        assert row.content_hash == content_hash_of(PDF_BYTES)

    async def test_an_unexpired_artifact_is_not_swept(self, session) -> None:  # noqa: ANN001
        cfg = _cfg(v3_artifact_retention_days=30)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await self._seed(session, cfg, retention_days=30, now=now)
        report = await expire_artifacts(
            session, cfg=cfg, now=now + timedelta(days=29), dry_run=False
        )
        assert report.candidates == 0

    async def test_the_corpus_flag_off_sweeps_nothing(self, session) -> None:  # noqa: ANN001
        report = await expire_artifacts(
            session,
            cfg=_cfg(v3_corpus_enabled=False),
            now=datetime(2099, 1, 1, tzinfo=timezone.utc),
            dry_run=False,
        )
        assert report.candidates == 0

    def test_nothing_in_the_codebase_schedules_a_sweep(self) -> None:
        # OPEN DECISION #12 is the user's. The primitive exists; the schedule does
        # not, and this test is what keeps it that way until the decision is made.
        callers = []
        for path in Path("app").rglob("*.py"):
            if path.name == "service.py" and "corpus/artifacts" in str(path):
                continue
            if "expire_artifacts" in path.read_text():
                callers.append(str(path))
        assert callers == [], callers


# --------------------------------------------------------------------------- #
# blob_path becomes a real retrieval path
# --------------------------------------------------------------------------- #


class TestBlobPathWiring:
    async def test_ingestion_persists_the_storage_key_on_the_document(self) -> None:
        from app.models.extracted_document import ExtractedDocument
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
        from app.services.sources.primary_document_extractor import (
            PrimaryDocumentExtraction,
        )

        cfg = Settings(
            v3_corpus_enabled=True,
            v3_artifact_store_backend=BACKEND_MEMORY,
            primary_document_ingestion_enabled=True,
            report_citation_persistence_enabled=True,
        )
        store = InMemoryArtifactStore()
        stored = await store_raw_artifact(
            PDF_BYTES,
            media_type="application/pdf",
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=cfg,
            store=store,
        )
        assert stored is not None
        artifact = PrimaryDocumentArtifact(
            source_url="https://pandoragroup.com/annual-report-2025.pdf",
            status="extracted",
            title="Annual Report 2025",
            extraction=PrimaryDocumentExtraction(
                content_hash=content_hash_of(PDF_BYTES),
                mime_type="application/pdf",
                extraction_method="native_pdf",
                status="extracted",
                page_count=169,
            ),
            raw_artifact=stored,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as s:
                await persist_primary_document_artifacts(
                    s,
                    artifacts=[artifact],
                    company_id=uuid.uuid4(),
                    agent_run_id=None,
                    cfg=cfg,
                )
                doc = (await s.execute(select(ExtractedDocument))).scalars().one()
                assert doc.blob_path == stored.storage_key
                assert is_valid_artifact_key(doc.blob_path)
                # And the path actually retrieves the document.
                assert await store.get(doc.blob_path or "") == PDF_BYTES
                # The lineage row exists alongside it.
                art = (await s.execute(select(ResearchArtifact))).scalars().one()
                assert art.content_hash == doc.content_hash
        finally:
            await engine.dispose()

    async def test_with_the_corpus_off_blob_path_stays_null(self) -> None:
        from app.models.extracted_document import ExtractedDocument
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
        from app.services.sources.primary_document_extractor import (
            PrimaryDocumentExtraction,
        )

        cfg = Settings(
            v3_corpus_enabled=False,
            primary_document_ingestion_enabled=True,
            report_citation_persistence_enabled=True,
        )
        artifact = PrimaryDocumentArtifact(
            source_url="https://pandoragroup.com/annual-report-2025.pdf",
            status="extracted",
            extraction=PrimaryDocumentExtraction(
                content_hash=content_hash_of(PDF_BYTES),
                mime_type="application/pdf",
                extraction_method="native_pdf",
                status="extracted",
            ),
        )
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as s:
                await persist_primary_document_artifacts(
                    s,
                    artifacts=[artifact],
                    company_id=uuid.uuid4(),
                    agent_run_id=None,
                    cfg=cfg,
                )
                doc = (await s.execute(select(ExtractedDocument))).scalars().one()
                assert doc.blob_path is None
                assert (await s.execute(select(ResearchArtifact))).scalars().all() == []
        finally:
            await engine.dispose()


# --------------------------------------------------------------------------- #
# Fetch-path integration
# --------------------------------------------------------------------------- #


class TestFetchPathIntegration:
    def _fetch_result(self):  # noqa: ANN202
        from app.services.sources.document_fetcher import DocumentFetchResult

        return DocumentFetchResult(
            requested_url="https://pandoragroup.com/ar.pdf",
            final_url="https://pandoragroup.com/ar.pdf",
            status_code=200,
            content_type="application/pdf",
            document_type="pdf",
            content=PDF_BYTES,
        )

    async def test_raw_bytes_are_retained_before_extraction_interprets_them(self) -> None:
        from app.services.sources import live_fetchers

        store = InMemoryArtifactStore()
        cfg = Settings(
            v3_corpus_enabled=True,
            v3_artifact_store_backend=BACKEND_MEMORY,
        )
        artifact = await live_fetchers._artifact_from_fetch(
            self._fetch_result(),
            title="Annual Report 2025",
            original_language=None,
            issuer_context=None,
            cfg=cfg,
            fetch_ms=10,
            artifact_store=store,
        )
        assert artifact.raw_artifact is not None
        assert artifact.raw_artifact.content_hash == content_hash_of(PDF_BYTES)
        assert artifact.raw_artifact.access_class == ACCESS_PUBLIC_ISSUER
        assert await store.get(artifact.raw_artifact.storage_key or "") == PDF_BYTES

    async def test_the_sec_leg_is_official_material_not_issuer_material(self) -> None:
        # The transport must never set the tier, and the fetcher must never guess
        # the data class: an EDGAR filing is public_official, an IR PDF is not.
        from app.services.sources import live_fetchers

        store = InMemoryArtifactStore()
        cfg = Settings(v3_corpus_enabled=True, v3_artifact_store_backend=BACKEND_MEMORY)
        artifact = await live_fetchers._artifact_from_fetch(
            self._fetch_result(),
            title="10-K",
            original_language=None,
            issuer_context=None,
            cfg=cfg,
            fetch_ms=10,
            access_class=ACCESS_PUBLIC_OFFICIAL,
            artifact_store=store,
        )
        assert artifact.raw_artifact is not None
        assert artifact.raw_artifact.access_class == ACCESS_PUBLIC_OFFICIAL

    async def test_with_the_corpus_off_the_fetch_path_is_unchanged(self) -> None:
        from app.services.sources import live_fetchers

        artifact = await live_fetchers._artifact_from_fetch(
            self._fetch_result(),
            title=None,
            original_language=None,
            issuer_context=None,
            cfg=Settings(v3_corpus_enabled=False),
            fetch_ms=10,
        )
        assert artifact.raw_artifact is None

    async def test_a_blocked_fetch_stores_nothing(self) -> None:
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        store = InMemoryArtifactStore()
        blocked = DocumentFetchResult(
            requested_url="https://example.invalid/x.pdf",
            blocked=True,
            error="host not allowlisted",
        )
        artifact = await live_fetchers._artifact_from_fetch(
            blocked,
            title=None,
            original_language=None,
            issuer_context=None,
            cfg=Settings(v3_corpus_enabled=True, v3_artifact_store_backend=BACKEND_MEMORY),
            fetch_ms=1,
            artifact_store=store,
        )
        assert artifact.raw_artifact is None
        assert store._objects == {}

    async def test_the_artifact_model_never_carries_document_bytes(self) -> None:
        # The founding invariant of PrimaryDocumentArtifact. What travels between
        # the fetch layer and the persistence layer is a hash, a key and a policy.
        from app.services.corpus.artifacts.store import StoredArtifact
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact

        for name, field in StoredArtifact.model_fields.items():
            assert field.annotation is not bytes, name
        assert "raw_artifact" in PrimaryDocumentArtifact.model_fields
