from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["environment"] == "development"
    assert data["version"] == "0.1.0"


async def test_health_response_schema(client: AsyncClient) -> None:
    response = await client.get("/health")
    data = response.json()
    # Phase 19.2.1: backward-compatible additive fields — existing consumers keep
    # status/environment/version; commit_sha/build_id were added for deploy checks.
    # Phase 27.1D: app/build_time added as safe deploy metadata for observability.
    assert set(data.keys()) == {
        "status",
        "environment",
        "version",
        "commit_sha",
        "build_id",
        "app",
        "build_time",
        "migrations",
    }


async def test_health_includes_build_metadata(client: AsyncClient) -> None:
    """commit_sha and build_id are always present strings (never null)."""
    response = await client.get("/health")
    data = response.json()
    assert isinstance(data["commit_sha"], str)
    assert isinstance(data["build_id"], str)
    # Locally there is no build_info.json, so both default to "unknown".
    assert data["commit_sha"]  # non-empty
    assert data["build_id"]  # non-empty


async def test_health_backward_compatible_fields(client: AsyncClient) -> None:
    """The original health contract fields must remain unchanged."""
    response = await client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "environment" in data
    assert "version" in data


async def test_health_reports_the_startup_migration(client: AsyncClient, tmp_path, monkeypatch) -> None:
    """startup.sh's outcome is shown; without the file the state is unknown."""
    from app.core import build_info

    monkeypatch.setattr(build_info, "MIGRATION_STATUS_FILE", str(tmp_path / "none.json"))
    data = (await client.get("/health")).json()
    assert data["migrations"] == {"state": "unknown", "revision": None, "at": None}

    status = tmp_path / "status.json"
    status.write_text('{"state":"ok","revision":"064","at":"2026-11-23T08:00:00Z"}')
    monkeypatch.setattr(build_info, "MIGRATION_STATUS_FILE", str(status))
    data = (await client.get("/health")).json()
    assert data["migrations"] == {"state": "ok", "revision": "064", "at": "2026-11-23T08:00:00Z"}

    status.write_text('{"state":"rm -rf","revision":"x"}')
    assert (await client.get("/health")).json()["migrations"]["state"] == "unknown"
