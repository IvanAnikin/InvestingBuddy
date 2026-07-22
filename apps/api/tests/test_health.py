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
