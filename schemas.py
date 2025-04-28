from pydantic import ( # Group imports from the same library
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    ConfigDict,
    FieldValidationInfo # Import FieldValidationInfo here
)
from typing import List, Optional, Any
from datetime import datetime
from enum import Enum
from bson import ObjectId
import os # Import os to access environment variables

# Import the custom ObjectId handler if needed for response models,
# but typically not needed for Create/Update schemas which use strings.
# from common import PyObjectId

# --- Enums ---
class UserRole(str, Enum):
    """Enumeration for user roles."""
    ADMIN = "admin"
    STUDENT_REP = "student_rep"

# --- Authentication Schemas ---
class Token(BaseModel):
    """Schema for the authentication token response."""
    access_token: str
    token_type: str

class TokenData(BaseModel):
    """Schema for data encoded within the JWT."""
    username: Optional[str] = None

# --- User Schemas ---
class UserBase(BaseModel):
    """Base schema for user properties."""
    email: EmailStr # Use EmailStr for automatic email format validation
    role: UserRole = UserRole.STUDENT_REP

class UserCreate(UserBase):
    """Schema for creating a new user (request body)."""
    password: str
    # Organization is expected as a string (ObjectId string) in the request
    organization: Optional[str] = None

    # Pydantic v2 validator for email domain
    @field_validator("email")
    @classmethod
    def validate_email_domain(cls, v: str) -> str:
        """Validates if the email belongs to the allowed domain."""
        allowed_domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
        # Only validate if the environment variable is set
        if allowed_domain and not v.endswith(allowed_domain):
            raise ValueError(f"Email must belong to the domain: {allowed_domain}")
        return v

    # Pydantic v2 validator for organization based on role
    # Use model_validator for cross-field validation
    @field_validator('organization')
    @classmethod
    def organization_required_for_student_rep(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        """Validates that organization is provided if role is student_rep."""
        # info.data contains the data being validated
        if 'role' in info.data and info.data['role'] == UserRole.STUDENT_REP and v is None:
            raise ValueError("Organization ID is required for student representatives")
        # Also check if the provided value is a valid ObjectId string if not None
        if v is not None:
            if not ObjectId.is_valid(v):
                 raise ValueError(f"Invalid ObjectId format for organization: {v}")
        return v


    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "email": "student@yourdomain.edu", # Use a placeholder or your actual domain
                "password": "strongpassword123",
                "role": "student_rep",
                "organization": "60d5ec9af682dbd12a0a9fb8" # Example ObjectId string
            }
        }
    )


class UserUpdate(BaseModel):
    """Schema for updating a user (request body - all fields optional)."""
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    # Organization is expected as a string (ObjectId string) in the request
    organization: Optional[str] = None

    # Optional: Add validation here too if needed for updates,
    # potentially checking if role changes require organization changes.
    # Example: Ensure organization is not set to None if role remains student_rep

    model_config = ConfigDict(
        arbitrary_types_allowed=True, # Might be needed if using complex types later
        json_schema_extra = {
            "example": {
                "email": "updated_student@yourdomain.edu",
                "organization": "60d5ec9af682dbd12a0a9fb9"
            }
        }
    )
class UserResponse(BaseModel):
    """Schema for returning user data in responses (excluding sensitive info)."""
    id: str = Field(..., alias="_id")  # Map MongoDB's _id to id
    email: EmailStr
    role: UserRole
    organization: Optional[str] = None

    model_config = ConfigDict(
        populate_by_name=True,  # Allows using alias '_id'
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "id": "680e64f6c7d6aa87969ead2d",
                "email": "sjm0481@dlsud.edu.ph",
                "role": "admin",
                "organization": None
            }
        }
    )

# --- Organization Schemas ---
class OrganizationBase(BaseModel):
    """Base schema for organization properties."""
    name: str
    description: Optional[str] = None

class OrganizationCreate(OrganizationBase):
    """Schema for creating a new organization (request body)."""
    # Representative is expected as a string (User ObjectId string)
    representative: Optional[str] = None

    # Optional: Add validator to check if representative ID is valid ObjectId format
    @field_validator('representative')
    @classmethod
    def validate_representative_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for representative: {v}")
        return v

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "name": "Computer Science Society",
                "description": "Organization for CS students",
                "representative": "60d5ec9af682dbd12a0a9fb7" # Example User ObjectId string
            }
        }
    )

class OrganizationUpdate(BaseModel):
    """Schema for updating an organization (request body - all fields optional)."""
    name: Optional[str] = None
    description: Optional[str] = None
    # Representative is expected as a string (User ObjectId string)
    representative: Optional[str] = None

    # Optional: Add validator to check if representative ID is valid ObjectId format
    @field_validator('representative')
    @classmethod
    def validate_representative_id_update(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for representative: {v}")
        return v

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "description": "Updated description for CS Org",
                "representative": "60d5ec9af682dbd12a0a9fb7"
            }
        }
    )


class OrganizationResponse(BaseModel):
    """Schema for returning organization data in responses."""
    # Use Field alias to map MongoDB's _id to 'id' in the response
    id: str = Field(..., alias="_id") # Use 'str' for the response ID
    name: str
    description: Optional[str] = None
    # Representative is returned as a string (User ObjectId string)
    representative: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        populate_by_name=True, # Allows using alias '_id'
        arbitrary_types_allowed=True, # Good practice for response models
        json_encoders={ObjectId: str}, # Ensure datetime is handled correctly if needed, though default is usually fine
        json_schema_extra = {
            "example": {
                "_id": "60d5ec9af682dbd12a0a9fb8",
                "name": "Computer Science Society",
                "description": "Organization for CS students",
                "representative": "60d5ec9af682dbd12a0a9fb7",
                "created_at": "2023-06-21T12:00:00Z", # Use ISO format for examples
                "updated_at": "2023-06-22T15:30:00Z"
            }
        }
    )

# Helper needed for the cross-field validator in UserCreate
from pydantic import FieldValidationInfo # Import at top if not already there
