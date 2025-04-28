# models.py
from pydantic import BaseModel, Field, ConfigDict, validator, EmailStr
from datetime import datetime
from typing import List, Optional, Any
from bson import ObjectId
from enum import Enum
import os
from dotenv import load_dotenv

# Import the custom ObjectId handler
from common import PyObjectId

# Load environment variables from .env file
load_dotenv()

# --- User Models ---

class UserRole(str, Enum):
    """Enumeration for user roles."""
    ADMIN = "admin"
    STUDENT_REP = "student_rep"

class UserBase(BaseModel):
    """Base model for User data, used for inheritance."""
    email: EmailStr # Use EmailStr for validation
    role: UserRole
    # Use PyObjectId for MongoDB ObjectId fields within internal models
    organization: Optional[PyObjectId] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True, # Allows custom types like PyObjectId
        json_encoders={ObjectId: str}, # How to encode ObjectId to JSON (usually string)
        populate_by_name=True # Allows using field alias (e.g., _id)
    )

class User(UserBase):
    """Model representing a User document in the database."""
    # Use PyObjectId and set alias for MongoDB's default _id field
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    hashed_password: str

    # Inherits model_config from UserBase, but can be extended if needed
    # No need to repeat ConfigDict settings unless overriding/adding

class UserCreateInternal(BaseModel):
    """
    Model specifically for creating a user internally,
    potentially after validating input from an API schema.
    Note: This differs from schema.UserCreate which takes string IDs.
    """
    email: EmailStr
    hashed_password: str # Store the hashed password
    role: UserRole
    organization: Optional[PyObjectId] = None # Use PyObjectId if linking directly

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Organization Models ---

class OrganizationBase(BaseModel):
    """Base model for Organization data."""
    name: str
    description: Optional[str] = None
    # Reference to User (student rep) using PyObjectId
    representative: Optional[PyObjectId] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class Organization(OrganizationBase):
    """Model representing an Organization document in the database."""
    # Use PyObjectId and set alias for MongoDB's default _id field
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    # Inherits model_config from OrganizationBase

class OrganizationCreateInternal(BaseModel):
    """
    Model specifically for creating an organization internally.
    Note: This differs from schema.OrganizationCreate which takes string IDs.
    """
    name: str
    description: Optional[str] = None
    representative: Optional[PyObjectId] = None # Use PyObjectId if linking directly

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Validators (Keep relevant validators if needed, e.g., for internal creation) ---
# Note: Validators are often better placed in the API schemas (schema.py)
# to validate incoming data before it reaches the database models.
# If you keep them here, ensure they apply to the correct model (e.g., UserCreateInternal).

# Example: If you had a specific internal creation model needing validation:
# class UserCreateInternal(BaseModel):
#     # ... fields ...
#     @validator("email")
#     def validate_email_domain(cls, v):
#         allowed_domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
#         if allowed_domain and not v.endswith(allowed_domain): # Check if domain is set
#             raise ValueError(f"Email must belong to the allowed domain.")
#         return v

#     @validator("organization", always=True) # Use always=True if depending on other fields
#     def organization_required_for_student_rep(cls, v, values):
#          # Check if 'role' exists in values before accessing
#         if 'role' in values and values.get("role") == UserRole.STUDENT_REP and v is None:
#             raise ValueError("Organization ObjectId is required for student representatives")
#         return v
