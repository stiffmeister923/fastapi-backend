from datetime import timedelta
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordRequestForm
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from auth.auth_handler import (
    authenticate_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_password_hash,
    verify_password
)
from auth.email_verification import (
    create_verification_token,
    store_verification_token,
    send_verification_email,
    verify_token,
    activate_user
)
from database import get_database
from schemas import Token, UserCreate, UserResponse, UserRole, UserCredentials, OrganizationResponse, OrganizationCreate
from modelsv1 import User, VerificationResponse
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
router = APIRouter(prefix="/auth", tags=["authentication"])

@router.post("/register", response_model=UserResponse)
async def register_user(user: UserCreate, db = Depends(get_database)):
    # Check if email already exists
    existing_user = await db.users.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    
    organization_id = None
    department = None

    if user.role == UserRole.STUDENT:
        if user.organization:
            try:
                org_id = ObjectId(user.organization)
                org = await db.organizations.find_one({"_id": org_id})
                if not org:
                    raise HTTPException(status_code=400, detail="Organization not found")
                organization_id = org_id
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid organization ID")
        else:
            raise HTTPException(
                status_code=400,
                detail="Organization ID is required for student users"
            )
    elif user.role == UserRole.ADMIN:
        if user.department:
            department = user.department
        else:
            raise HTTPException(
                status_code=400,
                detail="Department is required for admin users"
            )

    # Create and store the user
    hashed_password = get_password_hash(user.password)
    user_dict = {
        "email": user.email,
        "hashed_password": hashed_password,
        "role": user.role,
        "organization": organization_id,
        "department": department,
        "is_active": False  # New users are initially inactive
    }

    try:
        result = await db.users.insert_one(user_dict)
        user_id = result.inserted_id
        user_dict["_id"] = user_id

        # Generate and store verification token
        verification_token = create_verification_token(user.email)
        await store_verification_token(db, user_id, verification_token)

        # Construct verification URL (replace with your actual frontend URL)
        verification_url = f"{os.getenv('LOCAL_BACK')}/auth/verify?token={verification_token}"
        await send_verification_email(user.email, verification_url)

        # Update organization with representative if needed
        if organization_id and user.role == UserRole.STUDENT:
            await db.organizations.update_one(
                {"_id": organization_id},
                {"$addToSet": {"members": user_id}}
            )

        # Convert ObjectId to string for response
        user_response = {
            "_id": str(user_id),
            "email": user_dict["email"],
            "role": user_dict["role"],
            "organization": str(user_dict["organization"]) if user_dict["organization"] else None,
            "department": user_dict["department"]
        }
        return user_response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")




        

@router.post("/token", response_model=Token)
async def login_for_access_token(
    credentials: UserCredentials,
    db = Depends(get_database)
) -> Token:
    user = await authenticate_user(db, credentials.email, credentials.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    token_payload_data = {
        "sub": user["email"],
        "role": user["role"],
    }

    # Add role-specific claims, converting ObjectId to string if needed
    if user["role"] == UserRole.ADMIN.value and user.get("department"):
        token_payload_data["department"] = user["department"]
    elif user["role"] == UserRole.STUDENT.value and user.get("organization"):
        # --- Convert ObjectId to string HERE ---
        token_payload_data["organization"] = str(user["organization"]) 
        
    access_token = create_access_token(
        data=token_payload_data,
        expires_delta=access_token_expires
    )

    return Token(access_token=access_token, token_type="bearer")

@router.get("/verify", response_model=None)
async def verify_email(
    token: str, db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Verifies the email using the token sent to the user."""
    payload = await verify_token(db, token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired verification token",
        )

    email = payload.get("email")
    updated_user = await activate_user(db, email)
    if updated_user:
        return {"message": "Email successfully verified. You can now log in."}
    
    # Check if the user is already activated
    user = await db.users.find_one({"email": email})
    if user and user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is already activated"
        )
    
    # Fallback for other errors
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Error activating user account"
    )