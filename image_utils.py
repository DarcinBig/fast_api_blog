import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from github_storage import upload_image_to_github, delete_image_from_github

PROFILE_PICS_DIR = Path("media/profile_pics")


async def process_profile_image(content: bytes) -> str:
    """
    Process and upload a profile image to GitHub.
    Returns the raw GitHub URL of the profile image.
    """
    with Image.open(BytesIO(content)) as original:
        img = ImageOps.exif_transpose(original)

        img = ImageOps.fit(img, (300, 300), method=Image.Resampling.LANCZOS)

        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        # Save to an in-memory bytes buffer
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        buffer.seek(0)
        image_bytes = buffer.read()

    # Generate a unique filename
    filename = f"{uuid.uuid4().hex}.jpg"

    # Upload to GitHub and return the raw URL
    raw_url = await upload_image_to_github(image_bytes, filename)
    return raw_url


async def delete_profile_image(image_url: str | None) -> None:
    """Delete a profile image from GitHub given its raw URL."""
    if not image_url:
        return

    # Extract filename from URL.
    # URL pattern: https://raw.githubusercontent.com/owner/repo/branch/profile_pics/filename.jpg
    filename = image_url.split("/")[-1]
    await delete_image_from_github(filename)