"""Authentication router for OTP endpoints"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.auth.schemas import (
    SendOTPRequest,
    SendOTPResponse,
    VerifyOTPRequest,
    VerifyOTPResponse,
    InternalTokenRequest,
    InternalTokenResponse,
)
from app.auth.service import generate_and_send_otp, verify_otp
from app.core.database import get_database
from app.core.security import create_access_token
from app.core.internal_auth import require_internal_service_auth


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/send-otp", response_model=SendOTPResponse)
async def send_otp(request_data: SendOTPRequest, request: Request):
    """
    Send OTP to user's email.
    
    Rate limit: Max 3 OTP requests per email per hour.
    OTP expires in 10 minutes.
    """
    db = get_database()
    
    # Get client IP for audit trail (optional)
    client_ip = request.client.host if request.client else None
    
    result = await generate_and_send_otp(
        email=request_data.email,
        db=db,
        ip_address=client_ip
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return SendOTPResponse(
        success=True,
        message=result["message"]
    )


@router.post("/verify-otp", response_model=VerifyOTPResponse)
async def verify_otp_endpoint(request_data: VerifyOTPRequest):
    """
    Verify OTP and return user data for authentication.
    
    Max 3 verification attempts per OTP.
    Returns user object for NextAuth session creation.
    """
    db = get_database()
    
    result = await verify_otp(
        email=request_data.email,
        otp_code=request_data.otp,
        db=db
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    user = result["user"]
    access_token = create_access_token(
        user_id=user["userId"],
        name=user["name"],
    )
    return VerifyOTPResponse(
        id=user["id"],
        userId=user["userId"],
        name=user["name"],
        email=user["email"],
        access_token=access_token,
    )


@router.post("/internal/token", response_model=InternalTokenResponse)
async def issue_internal_access_token(
    request_data: InternalTokenRequest,
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """Issue backend JWT for trusted server-to-server auth exchange."""
    db = get_database()
    user = await db.users.find_one({"email": str(request_data.email).strip()})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = str(user.get("user_id") or user.get("email") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User identity is missing required user_id",
        )
    name = str(request_data.name or user.get("name") or user_id).strip()
    token = create_access_token(user_id=user_id, name=name)
    return InternalTokenResponse(access_token=token)

