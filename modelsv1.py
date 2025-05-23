# models.py
from pydantic import BaseModel, Field, ConfigDict, validator, EmailStr
from datetime import date, time, datetime, timezone
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
    department: Optional[str] = None
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

# --- Updated Schedule Model ---
class Schedule(BaseModel):
    """Model representing a Schedule document in the database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    event_id: PyObjectId # Link back to the event
    venue_id: PyObjectId # The venue where it's scheduled
    organization_id: Optional[PyObjectId] = None # <-- ADDED: Link to the organization
    scheduled_start_time: datetime # Combined date and start time
    scheduled_end_time: datetime   # Combined date and end time
    # Optional: Add a field to distinguish optimized schedules if stored in the same collection
    is_optimized: bool = Field(default=False) # <-- ADDED: Flag for GA results

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        populate_by_name=True
    )

# Optional: Update ScheduleCreateInternal if you use it elsewhere
class ScheduleCreateInternal(BaseModel):
    """Model for creating a schedule internally."""
    event_id: PyObjectId
    venue_id: PyObjectId
    organization_id: Optional[PyObjectId] = None # <-- ADDED
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    is_optimized: bool = False # <-- ADDED

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )
    
class EventRequestStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    NEEDS_ALTERNATIVES = "Needs_Alternatives"
    CANCELLED = "Cancelled"

# --- Updated Event Model ---
class Event(BaseModel):
    """Model representing an Event Request document in the database."""
    # Let MongoDB automatically generate the '_id' field upon insertion.
    
    event_name: str
    description: Optional[str] = None 
    
    # Link to Organization and User using PyObjectId internally
    organization_id: PyObjectId 
    requesting_user_id: PyObjectId 
    
    # Event details
    requires_funding: bool = False
    estimated_attendees: int = Field(0, ge=0) 
    
    # --- Change requested_date to datetime ---
    requested_date: datetime # Changed from date
    requested_time_start: datetime 
    requested_time_end: datetime   
    
    # Requested Venue (Primary) - Store as ID link
    requested_venue_id: Optional[PyObjectId] = None 
    
    # Note: Requested Equipment is handled via the separate EventEquipment collection
    
    # Status and Tracking
    approval_status: EventRequestStatus = EventRequestStatus.PENDING 
    admin_comment: Optional[str] = None
    request_document_key: Optional[str] = None 
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) 
    
    # Link to the final schedule (if approved)
    schedule_id: Optional[PyObjectId] = None 

    model_config = ConfigDict(
        arbitrary_types_allowed=True, 
        json_encoders={ObjectId: str}, 
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
    """Model representing event preferences (alternatives)."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id") 
    event_id: PyObjectId = Field(..., description="Link to the main event request") 
    preferred_venue_id: Optional[PyObjectId] = None 
    # --- Change time fields to datetime ---
    preferred_date: Optional[date] = None # Keep date for day preference
    preferred_time_slot_start: Optional[datetime] = None # Changed from time
    preferred_time_slot_end: Optional[datetime] = None   # Changed from time

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
    #id: PyObjectId = Field(default_factory=PyObjectId, alias="_id") # MongoDB needs an _id
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