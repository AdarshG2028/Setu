"""Shared FastAPI dependencies.

Two things live here, and the second is the point of the module.

`SessionDep` was previously copy-pasted into three route modules. It is
identical in all of them, so it belongs in one place.

`CurrentUserDep` is Phase 8's identity channel. Until now identity arrived
-- when it arrived at all -- as a field inside the request body:
`CreateProjectRequest.owner_id`, `PostMessageRequest.sender_id`. Five of
the seven /projects endpoints, including confirm-proposal, carried no
caller identity whatsoever.

**Why a header rather than the body.** Not a stylistic preference: FastAPI
resolves Pydantic bodies *after* dependencies run, so a Depends-based
membership guard structurally cannot read a body field. Identity has to
arrive somewhere a dependency can see it, which means a header (following
the Idempotency-Key precedent already in routes/jobs.py) or a token.

**What this is not.** It is not authentication. `X-User-Id` is asserted by
the client and believed; there is no users table, no credential, nothing
signed. Anyone can claim to be anyone. That is a deliberate scope
decision for this phase -- the collaboration logic Phase 8 is actually
about needs identity to be *consistent and checkable*, not *proven* --
but the honest description is "trust the client".

What it does buy: identity now enters the system through exactly one
function. Swapping in real auth later means changing `current_user_id`
and nothing else, rather than editing every route that reads a user id
out of a body.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.session import get_session

__all__ = [
    "CurrentUserDep",
    "ProjectMemberDep",
    "SessionDep",
    "ProjectOwnerDep",
    "current_user_id",
    "require_project_member",
    "require_project_owner",
]

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def current_user_id(
    x_user_id: Annotated[
        str,
        Header(
            alias="X-User-Id",
            description=(
                "Who is making this request. Asserted by the client and not "
                "verified -- see backend/api/deps.py. Any UUID identifies a "
                "user; there is no registration step."
            ),
        ),
    ],
) -> uuid.UUID:
    """The caller, as a UUID.

    Rejects a malformed value rather than coercing it: a user id that
    isn't a UUID would still be usable as a dict key and would silently
    create a parallel identity that never matches any stored row.
    """
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="X-User-Id must be a UUID",
        ) from exc


CurrentUserDep = Annotated[uuid.UUID, Depends(current_user_id)]


async def require_project_member(
    project_id: uuid.UUID, session: SessionDep, user_id: CurrentUserDep
) -> uuid.UUID:
    """Confirm the caller belongs to this room, and return their id.

    **404, never 403.** A 403 tells a stranger the project exists, which
    is itself information -- whether a given room id is real, and by
    extension whether someone else is working on it. A non-member and a
    non-existent project are indistinguishable from outside, which is the
    correct answer to both.

    A single indexed primary-key lookup, so applying it to every room
    endpoint costs one cheap query rather than a join.
    """
    from backend.repositories.project_member_repository import ProjectMemberRepository

    if not await ProjectMemberRepository(session).is_member(project_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )
    return user_id


ProjectMemberDep = Annotated[uuid.UUID, Depends(require_project_member)]


async def require_project_owner(
    project_id: uuid.UUID, session: SessionDep, user_id: CurrentUserDep
) -> uuid.UUID:
    """Stricter than membership: only the owner may invite.

    A member who could invite could hand the room to anyone, which makes
    the owner's control over who is present meaningless. V1 keeps that
    power in one place; Phase 9a's approval policies are where richer
    roles get designed, and inventing them before then would be guessing.

    404 rather than 403 for a non-member, for the reason in
    require_project_member. A *member* who is not the owner gets 403 --
    they already know the room exists, so there is nothing left to hide,
    and "you are not allowed" is the more useful answer.
    """
    from backend.repositories.project_member_repository import OWNER, ProjectMemberRepository

    role = await ProjectMemberRepository(session).get_role(project_id, user_id)
    if role is None or role not in ("owner", "member"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )
    if role != OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the project owner can invite members",
        )
    return user_id


ProjectOwnerDep = Annotated[uuid.UUID, Depends(require_project_owner)]
