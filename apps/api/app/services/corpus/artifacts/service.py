"""Raw-artifact retention, end to end — V3.1 Slice 1.1.

Binds three things that are deliberately separate everywhere else:

* the **policy** that says whether these bytes may be kept at all
  (:mod:`app.services.corpus.policy`);
* the **store** that holds them (:mod:`app.services.corpus.artifacts.store`);
* the **lineage row** that survives them
  (:class:`app.models.research_artifact.ResearchArtifact`).

WHY THE WRITE IS SPLIT IN TWO
=============================
:func:`store_raw_artifact` takes bytes and no session. :func:`record_artifact`
takes a session and no bytes. That split is not incidental — it preserves the
invariant ``PrimaryDocumentArtifact`` has carried since Phase 32A: *the artifact
that flows through the connector pipeline never contains raw document content*.
What flows between the two halves is a hash, a key and a policy, none of which
can leak a document.

The split is also safe to interrupt. The byte store is content-addressed and
idempotent, so a row written without its bytes is recoverable (the next run
stores them) and bytes written without their row are re-put as a deduplication
rather than as a second copy. Neither half can corrupt the other.

NOTHING HERE EVER RAISES INTO A RESEARCH RUN
============================================
A storage failure degrades to a lineage record with an honest ``failure_code``,
in the same spirit as every other ingestion failure in this codebase: the run
continues with no bytes retained rather than dying because a blob container was
briefly unavailable. Nothing is logged that could carry a byte, a key or a path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.research_artifact import ResearchArtifact
from app.services.corpus.artifacts.backends.local_fs import LocalFilesystemArtifactStore
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
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
    StoredArtifact,
    content_hash_of,
)
from app.services.corpus.policy import ArtifactPolicy, default_policy_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings

# Column guards. Values here are code-controlled, but clipping keeps a
# pathological media type from failing the INSERT for a whole research run.
_MEDIA_TYPE_MAX = 100
_STORAGE_KEY_MAX = 200
_BACKEND_MAX = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


# --------------------------------------------------------------------------- #
# Store resolution
# --------------------------------------------------------------------------- #


def resolve_artifact_store(cfg: "Settings") -> ArtifactStore | None:
    """Build the configured store, or ``None`` when bytes are not being retained.

    ``None`` is returned — with no error — when the corpus is off or the backend
    is ``none``. Those are the *default* configuration, so "no store" must be an
    ordinary answer rather than a failure: turning the corpus on does not by
    itself start writing bytes anywhere.

    A misconfigured backend (``local`` with no root, ``azure_blob`` with no
    account URL) also returns ``None`` rather than raising, and the caller records
    ``artifact_store_unavailable``. A research run must not die because a storage
    setting is missing.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return None
    backend = (getattr(cfg, "v3_artifact_store_backend", BACKEND_NONE) or "").strip().lower()
    max_bytes = max(1, int(getattr(cfg, "v3_artifact_max_bytes", 0) or 1))

    if backend in ("", BACKEND_NONE):
        return None
    if backend == BACKEND_MEMORY:
        return InMemoryArtifactStore(max_bytes=max_bytes)
    if backend == BACKEND_LOCAL:
        root = (getattr(cfg, "v3_artifact_store_local_root", "") or "").strip()
        if not root:
            return None
        return LocalFilesystemArtifactStore(root, max_bytes=max_bytes)
    if backend == BACKEND_AZURE_BLOB:
        account_url = (getattr(cfg, "v3_artifact_store_account_url", "") or "").strip()
        if not account_url:
            return None
        # Imported here so the Azure adapter — the one module allowed to know the
        # vendor exists — is not pulled in by every import of this service.
        from app.services.corpus.artifacts.backends.azure_blob import (
            AzureBlobArtifactStore,
            azure_container_client_factory,
        )

        container = (
            getattr(cfg, "v3_artifact_store_container", "") or ""
        ).strip() or "investingbuddy-documents"
        return AzureBlobArtifactStore(
            client_factory=azure_container_client_factory(
                account_url=account_url, container=container
            ),
            max_bytes=max_bytes,
        )
    # An unrecognised backend name is a configuration error, and guessing which
    # store was meant is worse than storing nothing.
    return None


# --------------------------------------------------------------------------- #
# Storing
# --------------------------------------------------------------------------- #


async def store_raw_artifact(
    data: bytes,
    *,
    media_type: str,
    access_class: str,
    cfg: "Settings",
    store: ArtifactStore | None = None,
    policy: ArtifactPolicy | None = None,
    now: datetime | None = None,
) -> StoredArtifact | None:
    """Retain ``data`` where policy permits; always describe what happened.

    Returns ``None`` — no work, no record — only when the corpus is disabled. In
    every other case a :class:`StoredArtifact` comes back, including when the
    bytes were deliberately or unavoidably NOT retained: the lineage record is the
    point, and a document that may not be retained keeps its lineage and loses its
    bytes.

    ``store`` is injectable so a test can supply an in-memory backend, and
    ``policy`` is injectable so a caller with a licence in hand can widen the
    deny-shaped default for a private artifact explicitly.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return None

    stamp = _as_aware(now or _utcnow())
    resolved_policy = policy or default_policy_for(
        access_class,
        retention_days=int(getattr(cfg, "v3_artifact_retention_days", 0) or 0),
        now=stamp,
    )
    digest = content_hash_of(data)
    resolved_media_type = (
        _clip(media_type or "application/octet-stream", _MEDIA_TYPE_MAX)
        or "application/octet-stream"
    )

    def _record(
        *,
        backend: str,
        storage_key: str | None = None,
        created: bool = False,
        deduplicated: bool = False,
        failure_code: str | None = None,
    ) -> StoredArtifact:
        """Every outcome of this function, with the lineage fields fixed.

        Written as one constructor so the "bytes were not retained" branches
        cannot drift from the successful one — every path records the same hash,
        the same size and the same policy, and differs only in where (or whether)
        the bytes went.
        """
        return StoredArtifact(
            content_hash=digest,
            byte_size=len(data),
            media_type=resolved_media_type,
            access_class=resolved_policy.access_class,
            policy_stored=resolved_policy.stored,
            policy_indexed=resolved_policy.indexed,
            policy_external_model=resolved_policy.sent_to_external_model,
            policy_quoted=resolved_policy.quoted,
            policy_retained_long_term=resolved_policy.retained_long_term,
            retention_expires_at=resolved_policy.retention_expires_at,
            backend=backend,
            storage_key=storage_key,
            created=created,
            deduplicated=deduplicated,
            failure_code=failure_code,
        )

    if not resolved_policy.may_store_bytes:
        return _record(backend=BACKEND_NONE, failure_code=FAILURE_POLICY_DENIED)

    backing = store if store is not None else resolve_artifact_store(cfg)
    if backing is None:
        return _record(backend=BACKEND_NONE, failure_code=FAILURE_STORE_UNAVAILABLE)

    try:
        put = await backing.put(data, media_type=resolved_media_type)
    except ArtifactTooLargeError:
        return _record(backend=BACKEND_NONE, failure_code=FAILURE_TOO_LARGE)
    except ArtifactConflictError:
        # Same digest, different byte length: a truncated earlier write or a
        # collision. Refusing to store is the fail-closed answer — this repository
        # has already paid once for treating a truncated artifact as authoritative.
        return _record(backend=BACKEND_NONE, failure_code=FAILURE_CONFLICT)
    except ArtifactStoreError:
        return _record(backend=BACKEND_NONE, failure_code=FAILURE_STORE_ERROR)
    except Exception:  # noqa: BLE001 - a store must never crash a research run
        return _record(backend=BACKEND_NONE, failure_code=FAILURE_STORE_ERROR)

    return _record(
        backend=_clip(put.ref.backend, _BACKEND_MAX) or BACKEND_NONE,
        storage_key=_clip(put.ref.storage_key, _STORAGE_KEY_MAX),
        created=put.created,
        deduplicated=put.deduplicated,
    )


# --------------------------------------------------------------------------- #
# Lineage persistence
# --------------------------------------------------------------------------- #


async def record_artifact(
    session: "AsyncSession",
    stored: StoredArtifact | None,
    *,
    cfg: "Settings",
    now: datetime | None = None,
) -> ResearchArtifact | None:
    """Upsert the lineage row for ``stored``. Flush-only; the caller commits.

    Idempotent on ``content_hash``: seeing the same document again bumps
    ``seen_count`` and ``last_seen_at`` rather than inserting a second row. That
    counter is the cheapest honest answer to "how much of a run is re-fetching
    documents we already hold", which V3.0's consumption telemetry can otherwise
    only guess at.

    An existing row that has no bytes and a call that DID store bytes upgrades the
    row: the storage key is filled in. The reverse never happens — a successful
    retention is not undone by a later failed one.
    """
    if stored is None or not getattr(cfg, "v3_corpus_enabled", False):
        return None

    stamp = _as_aware(now or _utcnow())
    existing = (
        await session.execute(
            select(ResearchArtifact)
            .where(ResearchArtifact.content_hash == stored.content_hash)
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.seen_count = int(existing.seen_count or 0) + 1
        existing.last_seen_at = stamp
        if stored.storage_key and not existing.storage_key:
            existing.storage_key = stored.storage_key
            existing.storage_backend = stored.backend
            existing.stored_at = stamp
            # Bytes are present again, so a prior deletion is no longer the
            # current state of this artifact.
            existing.bytes_deleted_at = None
        await session.flush()
        return existing

    row = ResearchArtifact(
        id=uuid.uuid4(),
        content_hash=stored.content_hash,
        byte_size=stored.byte_size,
        media_type=stored.media_type,
        storage_backend=stored.backend,
        storage_key=stored.storage_key,
        access_class=stored.access_class,
        policy_stored=stored.policy_stored,
        policy_indexed=stored.policy_indexed,
        policy_external_model=stored.policy_external_model,
        policy_quoted=stored.policy_quoted,
        policy_retained_long_term=stored.policy_retained_long_term,
        retention_expires_at=stored.retention_expires_at,
        stored_at=stamp if stored.storage_key else None,
        first_seen_at=stamp,
        last_seen_at=stamp,
        seen_count=1,
    )
    session.add(row)
    await session.flush()
    return row


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


async def load_artifact_bytes(
    session: "AsyncSession",
    *,
    content_hash: str,
    cfg: "Settings",
    store: ArtifactStore | None = None,
) -> bytes | None:
    """The raw bytes for a content hash, or ``None`` when they are not retained.

    ``None`` is an ordinary answer, not an error: bytes expire, policy forbids
    some of them, and lineage outlives all of them. Callers re-fetch or degrade
    honestly — never fabricate.

    The returned bytes are re-hashed before they are handed back. A store that
    returns the wrong object is not a hypothetical: a content-addressed read that
    trusts the key would silently hand a different document to a re-extraction,
    and every fact derived from it would carry the original document's citation.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return None
    row = (
        await session.execute(
            select(ResearchArtifact)
            .where(ResearchArtifact.content_hash == content_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or not row.storage_key:
        return None
    backing = store if store is not None else resolve_artifact_store(cfg)
    if backing is None:
        return None
    try:
        data = await backing.get(row.storage_key)
    except ArtifactStoreError:
        return None
    if data is None:
        return None
    if content_hash_of(data) != row.content_hash:
        # The store returned something that is not what the key promises. Refusing
        # is the only safe answer: silently returning it would attach one
        # document's citation to another document's text.
        return None
    return data


# --------------------------------------------------------------------------- #
# Retention (OPEN DECISION #12 — the primitive, never a schedule)
# --------------------------------------------------------------------------- #


@dataclass
class ExpiryReport:
    """What a retention sweep found, and what it did about it."""

    candidates: int = 0
    deleted: int = 0
    failed: int = 0
    dry_run: bool = True
    content_hashes: list[str] = field(default_factory=list)


async def expire_artifacts(
    session: "AsyncSession",
    *,
    cfg: "Settings",
    now: datetime | None = None,
    dry_run: bool = True,
    limit: int = 500,
) -> ExpiryReport:
    """Delete the BYTES of artifacts whose TTL has passed. Lineage always stays.

    ``dry_run=True`` is the default and reports candidates without touching
    anything, because deletion is irreversible and OPEN DECISION #12 — whether
    there should be a TTL at all — is still the user's to make. **Nothing in the
    codebase calls this on a schedule.** It exists so that when the decision is
    made, applying it is a configuration change and a deliberate run, not a
    migration and a new subsystem.

    Rows with ``retention_expires_at IS NULL`` are never candidates: NULL means no
    TTL is configured, which is the default, and a sweep that treated it as
    "expired" would delete the entire corpus the first time it ran.
    """
    report = ExpiryReport(dry_run=dry_run)
    if not getattr(cfg, "v3_corpus_enabled", False):
        return report
    stamp = _as_aware(now or _utcnow())
    rows = (
        (
            await session.execute(
                select(ResearchArtifact)
                .where(
                    ResearchArtifact.retention_expires_at.is_not(None),
                    ResearchArtifact.retention_expires_at <= stamp,
                    ResearchArtifact.storage_key.is_not(None),
                )
                .order_by(ResearchArtifact.retention_expires_at)
                .limit(max(1, int(limit)))
            )
        )
        .scalars()
        .all()
    )
    report.candidates = len(rows)
    report.content_hashes = [r.content_hash for r in rows]
    if dry_run or not rows:
        return report

    backing = resolve_artifact_store(cfg)
    for row in rows:
        key = row.storage_key
        if not key:
            continue
        try:
            if backing is not None:
                await backing.delete(key)
        except ArtifactStoreError:
            report.failed += 1
            continue
        row.storage_key = None
        row.storage_backend = BACKEND_NONE
        row.bytes_deleted_at = stamp
        report.deleted += 1
    await session.flush()
    return report


__all__ = [
    "ExpiryReport",
    "expire_artifacts",
    "load_artifact_bytes",
    "record_artifact",
    "resolve_artifact_store",
    "store_raw_artifact",
]
