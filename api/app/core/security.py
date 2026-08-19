"""Security utilities - encryption and OAuth state management"""

import jwt
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from cryptography.fernet import Fernet
from app.core.config import settings


class EncryptionService:
    """Handles encryption and decryption of sensitive tokens using Fernet"""

    def __init__(self):
        """Initialize cipher with key from environment"""
        if settings.encryption_key:
            self.cipher = Fernet(settings.encryption_key.encode())
        else:
            self.cipher = None

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string

        Args:
            plaintext: The string to encrypt

        Returns:
            Encrypted string (base64 encoded)
        """
        if not self.cipher:
            raise ValueError("Encryption key not configured")
        if not plaintext:
            raise ValueError("Cannot encrypt empty string")

        encrypted_bytes = self.cipher.encrypt(plaintext.encode())
        return encrypted_bytes.decode()

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt an encrypted string

        Args:
            ciphertext: The encrypted string to decrypt

        Returns:
            Decrypted plaintext string
        """
        if not self.cipher:
            raise ValueError("Encryption key not configured")
        if not ciphertext:
            raise ValueError("Cannot decrypt empty string")

        decrypted_bytes = self.cipher.decrypt(ciphertext.encode())
        return decrypted_bytes.decode()


class OAuthStateService:
    """Service for generating and validating OAuth state tokens using JWT"""

    def __init__(self):
        """Initialize OAuth state service with JWT secret"""
        self.secret = settings.jwt_secret_key
        self.algorithm = "HS256"
        self.expiration_minutes = 10

    def generate_state_token(
        self, 
        project_id: str, 
        user_id: str,
        return_nav: str = None,
        return_tab: str = None
    ) -> str:
        """
        Generate a signed JWT state token for OAuth flow

        Args:
            project_id: Project ID being integrated
            user_id: User ID performing the integration
            return_nav: Navigation state to return to after OAuth
            return_tab: Tab state to return to after OAuth

        Returns:
            Signed JWT state token
        """
        payload = {
            "project_id": project_id,
            "user_id": user_id,
            "nonce": secrets.token_urlsafe(16),
            "exp": datetime.utcnow() + timedelta(minutes=self.expiration_minutes),
            "iat": datetime.utcnow()
        }
        
        # Add navigation context if provided
        if return_nav:
            payload["return_nav"] = return_nav
        if return_tab:
            payload["return_tab"] = return_tab

        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def verify_state_token(self, state_token: str) -> Dict[str, Any]:
        """
        Verify and decode a state token

        Args:
            state_token: JWT state token to verify

        Returns:
            Decoded payload with project_id and user_id

        Raises:
            ValueError: If token is expired or invalid
        """
        try:
            payload = jwt.decode(
                state_token,
                self.secret,
                algorithms=[self.algorithm]
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("State token has expired")
        except jwt.InvalidTokenError as e:
            raise ValueError(f"Invalid state token: {str(e)}")


def create_access_token(user_id: str, name: str) -> str:
    """Issue a signed JWT access token for an authenticated user."""
    secret = str(getattr(settings, "jwt_secret_key", "") or "").strip()
    if not secret:
        raise ValueError("JWT secret key is not configured")
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id is required to create access token")
    normalized_name = str(name or "").strip()
    now = datetime.now(timezone.utc)
    expire_minutes = max(1, int(getattr(settings, "jwt_access_token_expire_minutes", 10080) or 10080))
    payload = {
        "sub": normalized_user_id,
        "user_id": normalized_user_id,
        "name": normalized_name,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token. Raises ValueError on failure."""
    secret = str(getattr(settings, "jwt_secret_key", "") or "").strip()
    if not secret:
        raise ValueError("JWT secret key is not configured")
    raw_token = str(token or "").strip()
    if not raw_token:
        raise ValueError("Missing access token")
    try:
        payload = jwt.decode(raw_token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid access token") from exc

    user_id = str(payload.get("user_id") or payload.get("sub") or "").strip()
    if not user_id:
        raise ValueError("Access token payload missing user_id")
    payload["user_id"] = user_id
    payload["name"] = str(payload.get("name") or "").strip()
    return payload


# Singleton instances
encryption_service = EncryptionService()
oauth_state_service = OAuthStateService()
