import jwt
import os
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
# Assuming database.py and schemas.py exist and are correctly defined
from database import get_database
from schemas import TokenData, UserResponse, UserRole
# Assuming modelsv1.py exists and User is defined
# from modelsv1 import User
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
# Security configurations
SECRET_KEY = os.getenv("SECRET_KEY", "default_secret_key_change_me") # Provide a default for safety
ALGORITHM = os.getenv("ALGORITHM", "HS256") # Provide a default algorithm

# Set token expiration to 24 hours (in minutes)
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 1440 minutes = 24 hours

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token") # Ensure this matches your actual token URL

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# APIRouter instance
router = APIRouter()

# --- Utility Functions ---

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hashes a plain password."""
    return pwd_context.hash(password)

async def get_user(db: AsyncIOMotorClient, email: str) -> dict | None:
    """Retrieves a user from the database by email."""
    user_collection = db.users
    user = await user_collection.find_one({"email": email})
    return user

async def authenticate_user(db: AsyncIOMotorClient, email: str, password: str) -> dict | bool:
    """Authenticates a user by email and password."""
    print(f"Authenticating user with email: {email}")
    user = await get_user(db, email)
    if not user:
        print(f"No user found for email: {email}")
        return False
    print(f"Found user: {user.get('_id')}") # Avoid printing sensitive info like hash
    if not verify_password(password, user.get("hashed_password", "")):
        print(f"Password verification failed for email: {email}")
        return False
    print(f"Authentication successful for email: {email}")
    # Return the user document (or a Pydantic model representation)
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Creates a JWT access token.

    Args:
        data: Data to encode in the token (typically {'sub': username}).
        expires_delta: Optional timedelta object for custom expiry.
                       If None, uses ACCESS_TOKEN_EXPIRE_MINUTES.

    Returns:
        The encoded JWT access token.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # Use the configured expiration time
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    # Ensure SECRET_KEY and ALGORITHM are loaded correctly
    if not SECRET_KEY or not ALGORITHM:
        raise ValueError("SECRET_KEY and ALGORITHM must be set in environment variables.")

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncIOMotorClient = Depends(get_database)) -> dict:
    """
    Decodes the JWT token, validates it, and retrieves the corresponding user.

    Args:
        token: The JWT token from the Authorization header.
        db: Database dependency.

    Returns:
        The user document from the database.

    Raises:
        HTTPException (401): If credentials cannot be validated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    print(f"Validating token: {token[:10]}...") # Log partial token for security

    # Ensure SECRET_KEY and ALGORITHM are loaded correctly
    if not SECRET_KEY or not ALGORITHM:
        print("Server configuration error: SECRET_KEY or ALGORITHM not set.")
        raise credentials_exception # Or a 500 error

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"Token payload: {payload}")
        email: str | None = payload.get("sub")
        if email is None:
            print("No 'sub' (subject/email) field in token payload")
            raise credentials_exception
        # You might not need TokenData schema here if you just need the email
        # token_data = TokenData(username=email)
        # print(f"Token data: {token_data}")
    except InvalidTokenError as e:
        print(f"Invalid token error: {str(e)}")
        raise credentials_exception
    except Exception as e: # Catch other potential errors during decoding
        print(f"An unexpected error occurred during token decoding: {str(e)}")
        raise credentials_exception

    # Use the email directly from the payload
    user = await get_user(db, email)
    if user is None:
        print(f"No user found for email extracted from token: {email}")
        raise credentials_exception

    print(f"Successfully validated token for user: {email}")
    # Return the user document (or a Pydantic model instance)
    # Consider returning a User model instance instead of a raw dict
    # return User(**user)
    return user

async def get_current_active_user(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency to get the current active user.
    You might add checks here later (e.g., if user is disabled).
    For now, it just returns the user obtained from get_current_user.
    """
    # Example check (add a 'disabled' field to your User model/document)
    # if current_user.get("disabled"):
    #     raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def require_admin(current_user: UserResponse = Depends(get_current_active_user)):
    """
    Dependency that raises an HTTPException if the current user is not an admin.
    Assumes get_current_active_user returns a dict-like object.
    """
    user_role = current_user.get("role")
    if not user_role or user_role != UserRole.ADMIN.value: # Compare with enum's value
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin privileges required."
        )
    return current_user

