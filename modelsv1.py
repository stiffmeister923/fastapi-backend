# models.py
from pydantic import BaseModel, Field, ConfigDict, validator, EmailStr
from datetime import date, time, datetime
from typing import List, Optional, Any
from bson import ObjectId
from enum import Enum
import os
from dotenv import load_dotenv

# Import the custom ObjectId handler
from common import PyObjectId

# Load environment variables from .env file
load_dotenv()

class VerificationResponse(BaseModel):
    message: str

# --- User Models ---


class UserRole(str, Enum):
    """Enumeration for user roles."""
    ADMIN = "admin"
    STUDENT = "student"

class UserBase(BaseModel):
    """Base model for User data, used for inheritance."""
    email: EmailStr # Use EmailStr for validation
    role: UserRole
    # Use PyObjectId for MongoDB ObjectId fields within internal models
    organization_id: Optional[PyObjectId] = Field(default=None, alias="organization")

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
    department: Optional[str] = None # Add the department here
    is_active: bool = False  # Add the is_active field
    verification_token: Optional[str] = None # Add the verification_token field

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
    organization_id: Optional[PyObjectId] = None # Use PyObjectId if linking directly
    department: Optional[str] = None # Add the department here

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Organization Models ---
class Organization(BaseModel):
    """Model representing an Organization document in the database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    name: str
    description: Optional[str] = None
    faculty_advisor_email: EmailStr  # Added faculty advisor email
    members: List[PyObjectId] = Field(default_factory=list) # List of member User IDs
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class OrganizationCreateInternal(BaseModel):
    """Model for creating an organization internally."""
    name: str
    description: Optional[str] = None
    faculty_advisor_email: EmailStr
    members: List[PyObjectId] = Field(default_factory=list)

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

# --- Schedule Models ---
class Schedule(BaseModel):
    """Model representing a Schedule document in the database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    venue_id: PyObjectId
    scheduled_date: date
    scheduled_time_start: time
    scheduled_time_end: time

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class ScheduleCreateInternal(BaseModel):
    """Model for creating a schedule internally."""
    venue_id: PyObjectId
    scheduled_date: date
    scheduled_time_start: time
    scheduled_time_end: time

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Events Models ---
class Event(BaseModel):
    """Model representing an Event document in the database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    event_name: str
    organization_id: PyObjectId
    requires_funding: bool = False
    estimated_attendees: int = 0
    requested_date: date
    requested_time_start: time
    requested_time_end: time
    approval_status: str = "Pending"  # Using str for simplicity, could be Enum later
    schedule_id: Optional[PyObjectId] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class EventCreateInternal(BaseModel):
    """Model for creating an event internally."""
    event_name: str
    organization_id: PyObjectId
    requires_funding: bool = False
    estimated_attendees: int = 0
    requested_date: date
    requested_time_start: time
    requested_time_end: time

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Preference Models ---
class Preference(BaseModel):
    """Model representing event preferences for the genetic algorithm."""
    event_id: PyObjectId = Field(..., alias="_id") # Using event_id as _id for direct link
    preferred_venue: Optional[str] = None
    preferred_date: Optional[date] = None
    preferred_time_slot_start: Optional[time] = None
    preferred_time_slot_end: Optional[time] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class PreferenceCreateInternal(BaseModel):
    """Model for creating preferences internally."""
    event_id: PyObjectId
    preferred_venue: Optional[str] = None
    preferred_date: Optional[date] = None
    preferred_time_slot_start: Optional[time] = None
    preferred_time_slot_end: Optional[time] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Venue Models ---
class Venue(BaseModel):
    """Model representing a Venue document in the database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    building: str
    venue_type: str
    occupancy: int
    code: str
    availability: str  # Could be Enum later (e.g., Available, Unavailable)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class VenueCreateInternal(BaseModel):
    """Model for creating a venue internally."""
    building: str
    venue_type: str
    occupancy: int
    code: str
    availability: str

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- Equipment Models ---
class Equipment(BaseModel):
    """Model representing an Equipment document in the database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    name: str
    availability: str  # Could be Enum later (e.g., Free, Assigned, Unavailable)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class EquipmentCreateInternal(BaseModel):
    """Model for creating equipment internally."""
    name: str
    availability: str

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# --- EventEquipment (Linking Table) Model ---
class EventEquipment(BaseModel):
    """Model representing the linking table between Events and Equipment."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id") # MongoDB needs an _id
    event_id: PyObjectId
    equipment_id: PyObjectId
    quantity: int = 1

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

class EventEquipmentCreateInternal(BaseModel):
    """Model for creating entries in the EventEquipment linking table."""
    event_id: PyObjectId
    equipment_id: PyObjectId
    quantity: int = 1

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )