import os
import re
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import respx
import httpx
from httpx import AsyncClient

from tests.conftest import auth_header, create_test_user, login_user


@pytest.mark.anyio
async def test_create_user_validation_error(client: AsyncClient):
    response = await client.post(
        "/api/users",
        json={"username": "testuser"},
    )
    assert response.status_code == 422
    assert "email" in response.text
    assert "password" in response.text


@pytest.mark.anyio
async def test_create_user_duplicate_email(client: AsyncClient):
    await create_test_user(client)
    response = await client.post(
        "/api/users",
        json={
            "username": "different_user",
            "email": "test@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"


@pytest.mark.anyio
async def test_create_user_success(client: AsyncClient):
    response = await client.post(
        "/api/users",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "securepassword123",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newuser"
    assert data["email"] == "newuser@example.com"
    assert "id" in data
    assert "image_path" in data
    assert "password" not in data
    assert "password_hash" not in data

@pytest.mark.anyio
async def test_upload_profile_picture(client: AsyncClient, mocked_github):
    user = await create_test_user(client)
    token = await login_user(client)

    test_image_path = Path(__file__).parent / "test_image.jpg"
    if not test_image_path.exists():
        pytest.skip("The file 'test_image.jpg' does not exist")

    image_bytes = test_image_path.read_bytes()

    # Build the regex dynamically from environment variables
    owner = os.environ["REPO_OWNER"]
    repo = os.environ["REPO_NAME"]
    # Escape dots in the URL (they are literal)
    pattern = rf"https://api\.github\.com/repos/{re.escape(owner)}/{re.escape(repo)}/contents/profile_pics/[a-f0-9]+\.jpg"

    # Mock the GET request (check for existing file) – return 404 (new file)
    mocked_github.get(url__regex=pattern).mock(
        return_value=httpx.Response(status_code=404)
    )

    # Mock the PUT request (upload new file)
    mock_put = mocked_github.put(url__regex=pattern).mock(
        return_value=httpx.Response(
            status_code=201,
            json={
                "content": {
                    "download_url": "https://raw.githubusercontent.com/test-owner/test-repo/main/profile_pics/abcdef123456.jpg"
                }
            }
        )
    )

    response = await client.patch(
        f"/api/users/{user['id']}/picture",
        files={"file": ("profile.jpg", BytesIO(image_bytes), "image/jpeg")},
        headers=auth_header(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["image_file"] is not None
    assert data["image_file"].startswith("https://raw.githubusercontent.com/")
    assert ".jpg" in data["image_file"]
    assert data["image_path"] == data["image_file"]
    assert mock_put.called

@pytest.mark.anyio
async def test_forgot_password_sends_email(client: AsyncClient):
    await create_test_user(client)
    with patch(
        "routers.users.send_password_reset_email",
        new_callable=AsyncMock,
    ) as mock_send:
        response = await client.post(
            "/api/users/forgot-password",
            json={"email": "test@example.com"},
        )
        assert response.status_code == 202
        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["to_email"] == "test@example.com"
        assert call_kwargs["username"] == "testuser"
        assert "token" in call_kwargs