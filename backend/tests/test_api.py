import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_app_config(client: AsyncClient):
    response = await client.get("/api/v1/config")
    assert response.status_code == 200
    data = response.json()
    assert "opengrok_base_url" in data

@pytest.mark.asyncio
async def test_list_repositories_empty(client: AsyncClient):
    response = await client.get("/api/v1/repositories")
    assert response.status_code == 200
    assert response.json() == []

@pytest.mark.asyncio
async def test_create_repository_invalid_url(client: AsyncClient):
    payload = {
        "repo_url": "invalid-url",
        "commit_id": "main"
    }
    response = await client.post("/api/v1/repository", json=payload)
    assert response.status_code == 422
