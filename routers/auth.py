from datetime import timedelta
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordRequestForm
from bson import ObjectId

from auth.auth_handler import (
    authenticate_user, 
    ACCESS_TOKEN_EXPIRE_MINUTES, 
    create_access_token, 
    get_user, 
    get_password_hash
)
from database import get_database
from schemas import Token, UserCreate, UserResponse, UserRole
from modelsv1 import User
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
    
    # For student reps, ensure organization exists if provided
    organization_id = None
    if user.organization:
        if user.role == UserRole.STUDENT_REP:
            try:
                org_id = ObjectId(user.organization)
                org = await db.organizations.find_one({"_id": org_id})
                if not org:
                    raise HTTPException(status_code=400, detail="Organization not found")
                organization_id = org_id
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid organization ID")
        else:
            # Non-student reps shouldn't have organizations
            raise HTTPException(
                status_code=400,
                detail="Only student representatives can be associated with organizations"
            )
    elif user.role == UserRole.STUDENT_REP:
        # Student reps must have an organization
        raise HTTPException(
            status_code=400, 
            detail="Organization is required for student representatives"
        )
    
    # Create and store the user
    hashed_password = get_password_hash(user.password)
    user_dict = {
        "email": user.email,
        "hashed_password": hashed_password,
        "role": user.role,
        "organization": organization_id,
    }
    
    try:
        result = await db.users.insert_one(user_dict)
        user_dict["_id"] = result.inserted_id
        
        # Update organization with representative if needed
        if organization_id and user.role == UserRole.STUDENT_REP:
            await db.organizations.update_one(
                {"_id": organization_id},
                {"$set": {"representative": result.inserted_id}}
            )
        
        # Convert ObjectId to string for response
        user_response = {
            "_id": str(user_dict["_id"]),
            "email": user_dict["email"],
            "role": user_dict["role"],
            "organization": str(user_dict["organization"]) if user_dict["organization"] else None
        }
        return user_response
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/sample")
async def root():
    return {"greeting": "Hello, World!", "message": "LESSSSSGAWWWW, fastapi backend  naten guys deployed to sa railway"}

@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db = Depends(get_database)
) -> Token:
    # Find user in database by username (which is email in our case)
    user = await authenticate_user(db, form_data.username, form_data.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password, bitch ass nigga",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["email"], "role": user["role"]},
        expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")