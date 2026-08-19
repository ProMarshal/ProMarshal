"""Plugin integration routes — Chrome extension auth."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth_deps import get_current_user

router = APIRouter(prefix="/api/integrations/plugin", tags=["plugin"])


@router.post("/token")
async def issue_plugin_token(
    current_user: dict = Depends(get_current_user),
):
    """Issue a long-lived plugin auth token for an authenticated user.

    The Chrome extension calls this after the user approves the connection
    on the /connect-plugin page. Returns a token the plugin stores locally
    to authenticate all subsequent API calls.
    """
    from app.core.security import create_access_token

    user_id = str(current_user.get("user_id") or "").strip()
    name = str(current_user.get("name") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not identify authenticated user",
        )
    token = create_access_token(user_id=user_id, name=name)
    return {"token": token, "user_id": user_id, "name": name}
