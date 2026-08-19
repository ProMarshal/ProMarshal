"""Authentication service for OTP generation and verification"""

import random
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.email import send_otp_email


async def generate_and_send_otp(
    email: str,
    db: AsyncIOMotorDatabase,
    ip_address: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate OTP, store in database, and send via email.
    
    Returns:
        dict: {"success": bool, "message": str}
    """
    # Check rate limiting: max 3 OTP requests per email per hour
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_otps = await db.otps.count_documents({
        "email": email,
        "created_at": {"$gte": one_hour_ago}
    })
    
    if recent_otps >= 3:
        return {
            "success": False,
            "message": "Too many OTP requests. Please try again in an hour."
        }
    
    # Generate 6-digit OTP
    otp_code = str(random.randint(100000, 999999))
    
    # Hash the OTP with bcrypt (cost factor 12)
    otp_hash = bcrypt.hashpw(otp_code.encode('utf-8'), bcrypt.gensalt(rounds=12))
    
    # Calculate expiration time (10 minutes from now)
    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(minutes=10)
    
    # Store in database
    otp_document = {
        "email": email,
        "otp_hash": otp_hash.decode('utf-8'),
        "created_at": created_at,
        "expires_at": expires_at,
        "verified": False,
        "attempts": 0,
    }
    
    if ip_address:
        otp_document["ip_address"] = ip_address
    
    await db.otps.insert_one(otp_document)
    
    # Send OTP via configured transactional email provider
    email_sent = await send_otp_email(email, otp_code)
    
    if not email_sent:
        return {
            "success": False,
            "message": "Failed to send OTP email. Please try again."
        }
    
    return {
        "success": True,
        "message": "OTP sent to your email. Please check your inbox."
    }


async def verify_otp(
    email: str,
    otp_code: str,
    db: AsyncIOMotorDatabase
) -> Dict[str, Any]:
    """
    Verify OTP code and return user data.
    
    Returns:
        dict: {"success": bool, "message": str, "user": dict (if success)}
    """
    # Find the latest unverified OTP for this email
    otp_record = await db.otps.find_one(
        {"email": email, "verified": False},
        sort=[("created_at", -1)]
    )
    
    if not otp_record:
        return {
            "success": False,
            "message": "No OTP found or already used. Please request a new one."
        }
    
    # Check if OTP has expired
    if datetime.utcnow() > otp_record["expires_at"]:
        return {
            "success": False,
            "message": "OTP has expired. Please request a new one."
        }
    
    # Check if max attempts exceeded
    if otp_record["attempts"] >= 3:
        return {
            "success": False,
            "message": "Too many verification attempts. Please request a new OTP."
        }
    
    # Verify OTP using bcrypt
    stored_hash = otp_record["otp_hash"].encode('utf-8')
    provided_otp = otp_code.encode('utf-8')
    
    if not bcrypt.checkpw(provided_otp, stored_hash):
        # Increment attempts counter
        await db.otps.update_one(
            {"_id": otp_record["_id"]},
            {"$inc": {"attempts": 1}}
        )
        
        attempts_remaining = 3 - (otp_record["attempts"] + 1)
        return {
            "success": False,
            "message": f"Invalid OTP. {attempts_remaining} attempt(s) remaining."
        }
    
    # OTP is valid! Mark as verified
    await db.otps.update_one(
        {"_id": otp_record["_id"]},
        {"$set": {"verified": True}}
    )
    
    # Get or create user in users collection
    user = await db.users.find_one({"email": email})
    
    if not user:
        # Auto-create user on first OTP login
        user_id = email  # Use full email as user_id for uniqueness
        user_name = email.split("@")[0].title()
        
        user_document = {
            "user_id": user_id,
            "name": user_name,
            "email": email,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db.users.insert_one(user_document)
        user_document["_id"] = result.inserted_id
        user = user_document
    
    # Return user data for NextAuth
    return {
        "success": True,
        "message": "OTP verified successfully",
        "user": {
            "id": str(user["_id"]),
            "userId": user["user_id"],
            "name": user["name"],
            "email": user["email"]
        }
    }

