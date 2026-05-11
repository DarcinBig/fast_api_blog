import base64
import httpx
from fastapi import HTTPException, status
from config import settings

GITHUB_API_URL = settings.github_api_url

async def upload_image_to_github(content: bytes, filename: str) -> str:
    """
    Upload an image to  the configured GitHub repository.
    Returns the raw URL of the uploaded image.
    """
    encoded = base64.b64encode(content).decode("utf-8")
    url = f"{GITHUB_API_URL}/repos/{settings.repo_owner}/{settings.repo_name}/contents/profile_pics/{filename}"
    headers = {
        "Authorization": f"token {settings.personal_access_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {
        "message": f"Upload profile picture {filename}",
        "content": encoded,
        "branch": settings.branch,
    }

    async with httpx.AsyncClient() as client:
        response = await client.put(url, headers=headers, json=data)

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"GitHub upload failed: {response.text}",
        )

    # Return the raw URL that can be used directly in <img src="...">
    raw_url = f"https://raw.githubusercontent.com/{settings.repo_owner}/{settings.repo_name}/{settings.branch}/profile_pics/{filename}"
    return raw_url

async def delete_image_from_github(filename: str) -> None:
    """
    Delete an image from the GitHub repository.
    """
    if not filename:
        return

    url = f"{GITHUB_API_URL}/repos/{settings.repo_owner}/{settings.repo_name}/contents/profile_pics/{filename}"
    headers = {
        "Authorization": f"token {settings.personal_access_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        # First, get the file's SHA (required for deletion)
        get_resp = await client.get(url, headers=headers)
        if get_resp.status_code == 404:
            return  # File doesn't exist, nothing to delete
        if get_resp.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get file SHA from GitHub: {get_resp.text}"
            )
        sha = get_resp.json().get("sha")

        # Perform deletion – use request() instead of delete(json=...)
        delete_data = {
            "message": f"Delete profile picture {filename}",
            "sha": sha,
            "branch": settings.branch,
        }
        del_resp = await client.request(
            "DELETE", url, headers=headers, json=delete_data
        )
        if del_resp.status_code not in (200, 204):
            # Log but don't crash – the image might already be gone
            print(f"Warning: could not delete {filename} from GitHub: {del_resp.text}")