import jwt
import os
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from database import get_database
from schemas import TokenData
from modelsv1 import User
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Security configurations
SECRET_KEY = os.getenv("SECRET_KEY")  # Generate a secure random key in production
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

router = APIRouter()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


async def get_user(db, email: str):
    user_collection = db.users
    user = await user_collection.find_one({"email": email})
    return user

async def authenticate_user(db, email: str, password: str):
    print(f"Authenticating user with email: {email}")
    user = await get_user(db, email)
    if not user:
        print(f"No user found for email: {email}")
        return False
    print(f"Found user: {user}")
    if not verify_password(password, user["hashed_password"]):
        print(f"Password verification failed for email: {email}")
        return False
    print(f"Authentication successful for email: {email}")
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme), db = Depends(get_database)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    print(f"Validating token: {token[:10]}...")  # Log partial token for security
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"Token payload: {payload}")
        email: str = payload.get("sub")
        if email is None:
            print("No 'sub' field in token payload")
            raise credentials_exception
        token_data = TokenData(username=email)
        print(f"Token data: {token_data}")
    except InvalidTokenError as e:
        print(f"Invalid token error: {str(e)}")
        raise credentials_exception

    user_collection = db.users
    user = await user_collection.find_one({"email": token_data.username})
    if user is None:
        print(f"No user found for email: {token_data.username}")
        raise credentials_exception
    print(f"Found user: {user}")
    return user

async def get_current_active_user(current_user = Depends(get_current_user)):
    return current_user