from datetime import timedelta, UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from PIL import UnidentifiedImageError
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from auth import (
    CurrentUser,
    create_access_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from config import settings
from database import get_db
from email_utils import send_password_reset_email
from image_utils import delete_profile_image, process_profile_image
from schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    PaginatedPostsResponse,
    PostResponse,
    ResetPasswordRequest,
    Token,
    UserCreate,
    UserPrivate,
    UserPublic,
    UserUpdate,
)

router = APIRouter()

@router.post("", response_model=UserPrivate, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    # ... (same as before) ...
    result = await db.execute(select(models.User).where(func.lower(models.User.username) == user.username.lower()))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Username already exists")
    result = await db.execute(select(models.User).where(func.lower(models.User.email) == user.email.lower()))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = models.User(username=user.username, email=user.email.lower(), password_hash=hash_password(user.password))
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(models.User).where(func.lower(models.User.email) == form_data.username.lower()))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    access_token = create_access_token(data={"sub": str(user.id)}, expires_delta=timedelta(minutes=settings.access_token_expire_minutes))
    return Token(access_token=access_token, token_type="bearer")


@router.get("/me", response_model=UserPrivate)
async def get_current_user(current_user: CurrentUser):
    return current_user


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(request_data: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Annotated[AsyncSession, Depends(get_db)]):
    # ... (unchanged) ...
    user = (await db.execute(select(models.User).where(func.lower(models.User.email) == request_data.email.lower()))).scalars().first()
    if user:
        await db.execute(sql_delete(models.PasswordResetToken).where(models.PasswordResetToken.user_id == user.id))
        token = generate_reset_token()
        reset_token = models.PasswordResetToken(user_id=user.id, token_hash=hash_reset_token(token), expires_at=datetime.now(UTC) + timedelta(minutes=settings.reset_token_expire_minutes))
        db.add(reset_token)
        await db.commit()
        background_tasks.add_task(send_password_reset_email, to_email=user.email, username=user.username, token=token)
    return {"message": "If an account exists with this email, you will receive password reset instructions."}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(request_data: ResetPasswordRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    # ... (unchanged) ...
    token_hash = hash_reset_token(request_data.token)
    reset_token = (await db.execute(select(models.PasswordResetToken).where(models.PasswordResetToken.token_hash == token_hash))).scalars().first()
    if not reset_token or reset_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user = (await db.execute(select(models.User).where(models.User.id == reset_token.user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.password_hash = hash_password(request_data.new_password)
    await db.execute(sql_delete(models.PasswordResetToken).where(models.PasswordResetToken.user_id == user.id))
    await db.commit()
    return {"message": "Password reset successfully. You can now log in with your new password."}


@router.patch("/me/password", status_code=status.HTTP_200_OK)
async def change_password(password_data: ChangePasswordRequest, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = hash_password(password_data.new_password)
    await db.execute(sql_delete(models.PasswordResetToken).where(models.PasswordResetToken.user_id == current_user.id))
    await db.commit()
    return {"message": "Password changed successfully"}


@router.get("/{user_id}", response_model=UserPublic)
async def get_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    user = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/{user_id}/posts", response_model=PaginatedPostsResponse)
async def get_user_posts(user_id: int, db: Annotated[AsyncSession, Depends(get_db)], skip: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = settings.posts_per_page):
    # ... (unchanged) ...
    if not (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first():
        raise HTTPException(status_code=404, detail="User not found")
    total = (await db.execute(select(func.count()).select_from(models.Post).where(models.Post.user_id == user_id))).scalar() or 0
    posts = (await db.execute(select(models.Post).options(selectinload(models.Post.author)).where(models.Post.user_id == user_id).order_by(models.Post.date_posted.desc()).offset(skip).limit(limit))).scalars().all()
    return PaginatedPostsResponse(posts=[PostResponse.model_validate(p) for p in posts], total=total, skip=skip, limit=limit, has_more=skip + len(posts) < total)


@router.patch("/{user_id}", response_model=UserPrivate)
async def update_user(user_id: int, user_update: UserUpdate, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this user")
    user = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_update.username and user_update.username.lower() != user.username.lower():
        if (await db.execute(select(models.User).where(func.lower(models.User.username) == user_update.username.lower()))).scalars().first():
            raise HTTPException(status_code=400, detail="Username already exists")
    if user_update.email and user_update.email.lower() != user.email.lower():
        if (await db.execute(select(models.User).where(func.lower(models.User.email) == user_update.email.lower()))).scalars().first():
            raise HTTPException(status_code=400, detail="Email already registered")
    for field, value in user_update.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this user")
    user = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    old_image = user.image_file
    await db.delete(user)
    await db.commit()
    # Delete from GitHub only if it's a GitHub URL
    if old_image and old_image.startswith(("http://", "https://")):
        await delete_profile_image(old_image)


# ---------- GITHUB PROFILE PICTURE ENDPOINTS ----------
@router.patch("/me/profile-picture", response_model=UserPrivate)
async def update_current_user_profile_picture(
    current_user: CurrentUser,
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Upload a new profile picture (stored on GitHub)."""
    if file.content_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only JPEG, PNG, GIF, WebP images are allowed")
    try:
        contents = await file.read()
        image_url = await process_profile_image(contents)   # returns GitHub raw URL
    except UnidentifiedImageError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image file")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    # Delete old GitHub image if it exists
    if current_user.image_file and current_user.image_file.startswith(("http://", "https://")):
        await delete_profile_image(current_user.image_file)
    current_user.image_file = image_url
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.delete("/me/profile-picture", response_model=UserPrivate)
async def delete_current_user_profile_picture(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove the profile picture (delete from GitHub)."""
    if not current_user.image_file or not current_user.image_file.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No GitHub-hosted profile picture to delete")
    await delete_profile_image(current_user.image_file)
    current_user.image_file = None
    await db.commit()
    await db.refresh(current_user)
    return current_user


# ---------- BACKWARD-COMPATIBLE ENDPOINTS (for existing frontend) ----------
@router.patch("/{user_id}/picture", response_model=UserPrivate)
async def update_user_profile_picture_compat(
    user_id: int,
    file: UploadFile,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """Legacy endpoint: PATCH /api/users/{user_id}/picture"""
    if current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    # Reuse the new logic
    return await update_current_user_profile_picture(current_user, file, db)


@router.delete("/{user_id}/picture", response_model=UserPrivate)
async def delete_user_profile_picture_compat(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """Legacy endpoint: DELETE /api/users/{user_id}/picture"""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await delete_current_user_profile_picture(current_user, db)