"""Authentication schemas for request/response validation"""

from pydantic import BaseModel, EmailStr


class SendOTPRequest(BaseModel):
    """Request schema for sending OTP"""
    email: EmailStr


class SendOTPResponse(BaseModel):
    """Response schema for send OTP endpoint"""
    success: bool
    message: str


class VerifyOTPRequest(BaseModel):
    """Request schema for verifying OTP"""
    email: EmailStr
    otp: str


class VerifyOTPResponse(BaseModel):
    """Response schema for verify OTP endpoint (user data for NextAuth)"""
    id: str
    userId: str
    name: str
    email: str
    access_token: str


class InternalTokenRequest(BaseModel):
    """Request schema for server-to-server internal token exchange."""
    email: EmailStr
    name: str = ""


class InternalTokenResponse(BaseModel):
    """Response schema containing backend JWT access token."""
    access_token: str

