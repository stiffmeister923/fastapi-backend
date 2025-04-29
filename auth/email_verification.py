import os
import logging
from datetime import datetime, timedelta
import jwt
from fastapi import Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorClient
from database import get_database
from schemas import UserResponse
from bson import ObjectId
from dotenv import load_dotenv
from typing import Optional

from mailjet_rest import Client
# Load environment variables
load_dotenv()
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS = 24

def create_verification_token(email: str) -> str:
    """Creates a unique verification token for the given email."""
    expire = datetime.utcnow() + timedelta(hours=EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS)
    to_encode = {"exp": expire, "sub": email}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def store_verification_token(db: AsyncIOMotorClient, user_id: ObjectId, token: str):
    """Stores the verification token in the user's database record."""
    await db.users.update_one(
        {"_id": user_id},
        {"$set": {"verification_token": token, "is_active": False}}
    )

async def verify_token(db: AsyncIOMotorClient, token: str) -> Optional[dict]:
    """Verifies the token and returns the user's email if valid."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid verification token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        return {"email": email}
    except Exception:
        raise credentials_exception

async def activate_user(db: AsyncIOMotorClient, email: str) -> Optional[UserResponse]:
    """Activates the user account by setting is_active to True and removing the token."""
    logger.info(f"Activating user with email: {email}")
    user = await db.users.find_one({"email": email})
    if not user:
        logger.warning(f"User not found: {email}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if user.get("is_active", False):
        logger.info(f"User already activated: {email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already activated"
        )

    update_result = await db.users.update_one(
        {"email": email, "is_active": False},
        {"$set": {"is_active": True}, "$unset": {"verification_token": ""}}
    )

    logger.info(f"Update result: modified_count={update_result.modified_count}")
    if update_result.modified_count == 1:
        updated_user = await db.users.find_one({"email": email})
        return UserResponse(**{**updated_user, "id": str(updated_user["_id"])})
    else:
        logger.warning(f"Failed to activate user: {email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to activate user, possibly already activated"
        )

async def send_verification_email(email: str, verification_url: str):
    """Sends the verification email using Mailjet API."""
    api_key = os.getenv("MAILJET_API_KEY")
    api_secret = os.getenv("MAILJET_API_SECRET")
    sender_email = os.getenv("MAILJET_SENDER_EMAIL")
    sender_name = os.getenv("MAILJET_SENDER_NAME", "Event Scheduler Team")

    if not all([api_key, api_secret, sender_email]):
        print("Mailjet API keys or sender email not configured in .env file.")
        return

    mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    data = {
        'Messages': [
            {
                "From": {
                    "Email": sender_email,
                    "Name": sender_name
                },
                "To": [
                    {
                        "Email": email,
                        "Name": ""
                    }
                ],
                "Subject": "Verify Your Account",
                "TextPart": f"""Please click the link below to verify your account:\n\n{verification_url}\n\nThis link will expire in 24 hours.""",
                "HTMLPart": f"""<h3>Please click the link below to verify your account:</h3><p><a href="{verification_url}">{verification_url}</a></p><p>This link will expire in 24 hours.</p>"""
            }
        ]
    }
    try:
        result = mailjet.send.create(data=data)
        if result.status_code == 201:
            print(f"Verification email sent to {email} via Mailjet")
        else:
            print(f"Error sending email to {email} via Mailjet: {result.status_code} - {result.json()}")
    except Exception as e:
        print(f"Error sending email to {email} via Mailjet: {e}")