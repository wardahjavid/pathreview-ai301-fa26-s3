from uuid import UUID

import structlog
from sqlalchemy import select

from api.schemas.profile import ProfileCreate, ProfileUpdate
from core.models.ingested_source import IngestedSource
from core.models.profile import Profile
from core.models.review import Review

log = structlog.get_logger()


async def create_profile(
    db,
    user_id: UUID,
    data: ProfileCreate,
    resume_filename: str = None,
    resume_text: str = None,
) -> Profile:
    """Create a new profile for a user.

    Args:
        db: Async database session used to add and commit the profile.
        user_id: ID of the user who will own the profile.
        data: Profile fields from the request (GitHub username and
            portfolio URL).
        resume_filename: Original filename of the uploaded resume, if any.
        resume_text: Extracted text of the uploaded resume, if any.

    Returns:
        The newly created profile, refreshed from the database.
    """
    profile = Profile(
        user_id=user_id,
        github_username=data.github_username,
        portfolio_url=data.portfolio_url,
        resume_filename=resume_filename,
        resume_text=resume_text,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def get_profile(
    db,
    profile_id: UUID,
    user_id: UUID,
) -> Profile | None:
    """Get a profile by ID, checking that the user owns it.

    Args:
        db: Async database session used for the query.
        profile_id: ID of the profile to fetch.
        user_id: ID of the user who must own the profile.

    Returns:
        The matching profile, or None if no profile with this ID
        belongs to the user.
    """
    stmt = select(Profile).where((Profile.id == profile_id) & (Profile.user_id == user_id))
    result = await db.execute(stmt)
    return result.scalars().first()


async def update_profile(
    db,
    profile_id: UUID,
    user_id: UUID,
    data: ProfileUpdate,
) -> Profile | None:
    """Update a profile, checking that the user owns it.

    Only fields that are set (not None) in ``data`` are changed.

    Args:
        db: Async database session used to save the changes.
        profile_id: ID of the profile to update.
        user_id: ID of the user who must own the profile.
        data: New field values; fields left as None are not changed.

    Returns:
        The updated profile, refreshed from the database, or None if no
        profile with this ID belongs to the user.
    """
    profile = await get_profile(db, profile_id, user_id)
    if not profile:
        return None

    if data.github_username is not None:
        profile.github_username = data.github_username
    if data.portfolio_url is not None:
        profile.portfolio_url = data.portfolio_url

    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def delete_profile(
    db,
    profile_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a profile and cascade delete its reviews and ingested sources.

    Args:
        db: Async database session used for the deletes.
        profile_id: ID of the profile to delete.
        user_id: ID of the user who must own the profile.

    Returns:
        True if the profile and its related records were deleted, False
        if no profile with this ID belongs to the user.

    Raises:
        Exception: Any error raised while deleting the related records or
            the profile. The session is rolled back and the original
            exception is re-raised.
    """
    profile = await get_profile(db, profile_id, user_id)
    if not profile:
        return False

    try:
        # Delete related reviews
        stmt = select(Review).where(Review.profile_id == profile_id)
        result = await db.execute(stmt)
        reviews = result.scalars().all()
        for review in reviews:
            await db.delete(review)

        # Delete related ingested sources
        stmt = select(IngestedSource).where(IngestedSource.profile_id == profile_id)
        result = await db.execute(stmt)
        sources = result.scalars().all()
        for source in sources:
            await db.delete(source)

        # Delete profile
        await db.delete(profile)
        await db.commit()

        log.info("profile_deleted_cascade", profile_id=str(profile_id))
        return True

    except Exception as exc:
        log.error("profile_cascade_delete_failed", profile_id=str(profile_id), error=str(exc))
        await db.rollback()
        raise
