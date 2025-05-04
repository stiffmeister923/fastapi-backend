from pydantic import ( # Group imports from the same library
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    ConfigDict,
    FieldValidationInfo # Import FieldValidationInfo here
)
from typing import List, Optional, Any
from datetime import date, time, datetime, timezone
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
    STUDENT= "student"

# --- Authentication Schemas ---
class Token(BaseModel):
    """Schema for the authentication token response."""
    access_token: str
    token_type: str

class TokenData(BaseModel):
    """Schema for data encoded within the JWT."""
    username: Optional[str] = None

class UserCredentials(BaseModel):
    email: str
    password: str

# --- User Schemas ---
class UserBase(BaseModel):
    """Base schema for user properties."""
    email: EmailStr # Use EmailStr for automatic email format validation
    role: UserRole = UserRole.STUDENT
    organization: Optional[str] = None

class UserCreate(UserBase):
    """Schema for creating a new user (request body)."""
    password: str
    department: Optional[str] = None # Add department for admin creation

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
    def organization_required_for_student(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        """Validates that organization is provided if role is student."""
        # info.data contains the data being validated
        if 'role' in info.data and info.data['role'] == UserRole.STUDENT and v is None:
            raise ValueError("Organization ID is required for students")
        # Also check if the provided value is a valid ObjectId string if not None
        if v is not None:
            if not ObjectId.is_valid(v):
                 raise ValueError(f"Invalid ObjectId format for organization: {v}")
        return v

    # Pydantic v2 validator for department based on role
    @field_validator('department')
    @classmethod
    def department_required_for_admin(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        """Validates that department is provided if role is admin."""
        if 'role' in info.data and info.data['role'] == UserRole.ADMIN and v is None:
            raise ValueError("Department is required for administrators")
        return v

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "email": "student@yourdomain.edu", # Use a placeholder or your actual domain
                "password": "strongpassword123",
                "role": "student",
                "organization": "60d5ec9af682dbd12a0a9fb8" # Example ObjectId string
                # For admin:
                # "role": "admin",
                # "department": "College of Science"
            }
        }
    )


class UserUpdate(UserBase):
    """Schema for updating a user (request body - all fields optional)."""
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    organization: Optional[str] = None
    department: Optional[str] = None # Add department for admin updates

    # Optional: Add validation here too if needed for updates,
    # potentially checking if role changes require organization/department changes.
    # Example: Ensure organization is not set to None if role remains student

    model_config = ConfigDict(
        arbitrary_types_allowed=True, # Might be needed if using complex types later
        json_schema_extra = {
            "example": {
                "email": "updated_student@yourdomain.edu",
                "organization": "60d5ec9af682dbd12a0a9fb9",
                "department": "Faculty of Arts and Sciences" # Example for admin update
            }
        }
    )

class UserResponse(BaseModel):
    """Schema for returning user data in responses (excluding sensitive info)."""
    id: str 
    email: EmailStr
    role: UserRole
    organization: Optional[str] = None
    department: Optional[str] = None
    is_active: bool = False  # Add the is_active field to the response

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "id": "680e64f6c7d6aa87969ead2d",
                "email": "sjm0481@dlsud.edu.ph",
                "role": "admin",
                "organization": None,
                "department": "CICS",
                "is_active": False
            }
        }
    )

# --- Organization Schemas ---
class OrganizationBase(BaseModel):
    """Base schema for organization properties."""
    name: str
    description: Optional[str] = None
    faculty_advisor_email: EmailStr
    department: Optional[str] = None

class OrganizationCreate(OrganizationBase):
    """Schema for creating a new organization (request body)."""
    # Members will be added later, not during initial creation
    pass

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "name": "Computer Science Society",
                "description": "Organization for CS students",
                "faculty_advisor_email": "faculty@yourdomain.edu"
            }
        }
    )

class OrganizationUpdate(BaseModel):
    """Schema for updating an organization (request body - all fields optional)."""
    name: Optional[str] = None
    description: Optional[str] = None
    faculty_advisor_email: Optional[EmailStr] = None
    department: Optional[str] = None
    # Members will be updated through a separate mechanism

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "description": "Updated description for CS Org",
                "faculty_advisor_email": "updated_faculty@yourdomain.edu"
            }
        }
    )


class OrganizationResponse(BaseModel):
    """Schema for returning organization data in responses."""
    # Use Field alias to map MongoDB's _id to 'id' in the response
    id: str = Field(..., alias="_id") # Use 'str' for the response ID
    name: str
    description: Optional[str] = None
    faculty_advisor_email: EmailStr
    department: Optional[str] = None
    members: List[str] # List of member User IDs as strings for response
    events: List[str]
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
                "faculty_advisor_email": "faculty@yourdomain.edu",
                "members": ["60d5ec9af682dbd12a0a9fb7"],
                "events":["56123123"],
                "created_at": "2023-06-21T12:00:00Z", # Use ISO format for examples
                "updated_at": "2023-06-22T15:30:00Z"
            }
        }
    )

# Helper needed for the cross-field validator in UserCreate
from pydantic import FieldValidationInfo # Import at top if not already there

 #--- Updated Schedule Schemas ---
class ScheduleBase(BaseModel):
    """Base schema for schedule properties."""
    venue_id: str = Field(..., description="ID of the scheduled venue")
    # ADD organization_id here for clarity in response/base
    organization_id: Optional[str] = Field(None, description="ID of the associated organization")
    scheduled_start_time: datetime = Field(..., description="Scheduled start date and time (ISO 8601 format)")
    scheduled_end_time: datetime = Field(..., description="Scheduled end date and time (ISO 8601 format)")
    # event_id is usually not needed for creation via API, but added in response

    # Optional: Add validator for end time > start time
    @field_validator('scheduled_end_time')
    @classmethod
    def validate_end_after_start(cls, v: datetime, info: FieldValidationInfo) -> datetime:
        start_time = info.data.get('scheduled_start_time')
        if start_time and v <= start_time:
            raise ValueError("Scheduled end time must be after scheduled start time")
        return v

    # Add validation for organization_id format if provided and needed
    @field_validator("organization_id")
    @classmethod
    def validate_org_id_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for organization_id: {v}")
        return v

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
         json_encoders={ # Ensure consistent serialization for examples/input
            ObjectId: str,
            datetime: lambda dt: dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') if isinstance(dt, datetime) else None,
        }
    )

class ScheduleCreate(ScheduleBase):
    """Schema for creating a schedule (potentially via a dedicated endpoint, though not used here)."""
    # Usually linked to an event, so event_id might be needed depending on API design
    # event_id: str = Field(..., description="ID of the event being scheduled")
    pass # Inherits fields from ScheduleBase

class ScheduleResponse(ScheduleBase):
    """Schema for returning schedule data in API responses."""
    id: str = Field(..., alias="_id", description="Unique ID of the schedule entry")
    event_id: str = Field(..., description="ID of the associated event") # Add event_id to response
    # organization_id is inherited from ScheduleBase
    # Add is_optimized flag to response if needed for frontend logic
    is_optimized: bool = Field(default=False, description="Indicates if this schedule is from the optimizer")


    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ # Ensure consistent serialization for output
            ObjectId: str,
            datetime: lambda dt: dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') if isinstance(dt, datetime) else None,
            # date: lambda d: d.isoformat() if isinstance(d, date) else None # Keep if date objects are used elsewhere
        },
        json_schema_extra={
            # --- UPDATED EXAMPLE ---
            "example": {
                "_id": "681a0b1c2d3e4f5a6b7c8d9e",
                "event_id": "6812e9c94c795e2a0717f49d",
                "venue_id": "68129f0c6d0bee76fee415e8",
                "organization_id": "60d5ec9af682dbd12a0a9fb8", # Example Org ID
                "scheduled_start_time": "2025-11-28T13:00:00Z",
                "scheduled_end_time": "2025-11-28T17:00:00Z",
                "is_optimized": False
            }
        }
        )

class ScheduleUpdate(BaseModel):
    """Schema for updating a schedule (optional fields)."""
    venue_id: Optional[str] = None
    organization_id: Optional[str] = None # <-- ADDED
    scheduled_start_time: Optional[datetime] = None
    scheduled_end_time: Optional[datetime] = None
    is_optimized: Optional[bool] = None # <-- ADDED

    # Add validation for organization_id format if provided
    @field_validator("organization_id")
    @classmethod
    def validate_org_id_format_update(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for organization_id: {v}")
        return v

    model_config = ConfigDict(arbitrary_types_allowed=True)

class ScheduleEventInfoRequestItem(BaseModel):
    """Schema for each item in the request body for the event name endpoint."""
    # Include all fields from the user's example request
    venue_id: str
    organization_id: str
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    _id: str # Assuming this is the schedule ID
    event_id: str
    is_optimized: bool

    # Add validators for ID formats
    @field_validator("venue_id", "organization_id", "_id", "event_id")
    @classmethod
    def validate_objectid_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format: {v}")
        return v

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda dt: dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') if isinstance(dt, datetime) else None,
        }
    )

class ScheduleEventInfoResponseItem(ScheduleEventInfoRequestItem):
    """Schema for each item in the response body, adding the event name."""
    event_name: Optional[str] = Field(None, description="Name of the associated event")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda dt: dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') if isinstance(dt, datetime) else None,
        }
    )
# --- END NEW Schemas ---

class EventRequestStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    NEEDS_ALTERNATIVES = "Needs_Alternatives"
    CANCELLED = "Cancelled"


# --- Add this Schema for Requested Equipment ---
class RequestedEquipmentItem(BaseModel):
    """Schema for an item in the list of requested equipment."""
    equipment_id: str = Field(..., description="ID of the requested equipment item")
    quantity: int = Field(..., gt=0, description="Quantity of the equipment item needed") # Ensure quantity is positive

    # Add validation for equipment_id format if desired
    @field_validator("equipment_id")
    @classmethod
    def validate_equipment_id(cls, v: str) -> str:
        if not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for equipment_id: {v}")
        return v

# --- Events Schemas ---
class EventBase(BaseModel):
    """Base schema for event request properties."""
    event_name: str = Field(..., min_length=3, max_length=100, description="Name of the event")
    #organization_id: str # Removed - will be inferred from user
    requires_funding: bool = False
    estimated_attendees: int = Field(0, ge=0) # Ensure non-negative
    # Ensure correct types are used
    requested_date: datetime = Field(..., description="Preferred date for the event (YYYY-MM-DD)")
    requested_time_start: datetime = Field(..., description="Preferred start time (HH:MM:SS)")
    requested_time_end: datetime = Field(..., description="Preferred end time (HH:MM:SS)")
    # Add description field if needed by frontend/backend logic
    description: Optional[str] = Field(None, max_length=500, description="Optional detailed description") 

    model_config = ConfigDict(arbitrary_types_allowed=True)

class EventCreate(EventBase):
    """Schema used for creating a new event request via the API."""
    # Add fields for specific requests
    requested_venue_id: Optional[str] = Field(None, description="ID of the initially requested venue (optional)")
    requested_equipment: Optional[List[RequestedEquipmentItem]] = Field(None, description="List of requested equipment items (optional)")

    # Add validation for requested_venue_id format if provided
    @field_validator("requested_venue_id")
    @classmethod
    def validate_venue_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for requested_venue_id: {v}")
        return v
        
    model_config = ConfigDict(
         json_schema_extra = { # Example for API docs
            "example": {
                "event_name": "Annual Programming Contest",
                "description": "Coding competition for students.",
                "requires_funding": True,
                "estimated_attendees": 50,
                "requested_date": "2025-11-15",
                "requested_time_start": "09:00:00",
                "requested_time_end": "15:00:00",
                "requested_venue_id": "60d5f1b4f682dbd12a0a9fc1", # Example Venue ID
                "requested_equipment": [
                    {"equipment_id": "60d5f2a0f682dbd12a0a9fc5", "quantity": 10}, # Example Equipment ID & Qty
                    {"equipment_id": "60d5f2a0f682dbd12a0a9fc6", "quantity": 1}
                ]
            }
        }
    )

class RequestedEquipmentItem(BaseModel):
    """Schema for an item in the list of requested equipment."""
    equipment_id: str = Field(..., description="ID of the requested equipment item")
    quantity: int = Field(..., gt=0, description="Quantity of the equipment item needed") # Ensure quantity is positive

    # Add validation for equipment_id format if desired
    @field_validator("equipment_id")
    @classmethod
    def validate_equipment_id(cls, v: str) -> str:
        if not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format for equipment_id: {v}")
        return v

class EventResponse(EventBase):
    """Schema for returning event data in API responses."""
    id: str = Field(..., alias="_id", description="Unique ID of the event request")
    organization_id: str = Field(..., description="ID of the requesting organization")
    requesting_user_id: str = Field(..., description="ID of the user who submitted the request")
    approval_status: EventRequestStatus = Field(..., description="Current status of the request") # Use Enum
    admin_comment: Optional[str] = None
    schedule_id: Optional[str] = Field(None, description="ID of the associated schedule (if approved and scheduled)")
    request_document_key: Optional[str] = Field(None, description="S3 key for the uploaded request document (if any)")
    # Add requested venue/equipment if needed in response
    requested_venue_id: Optional[str] = None 
    # Note: Displaying requested equipment might require another query or embedding
    requested_equipment: Optional[List[RequestedEquipmentItem]] = None # Decide if needed
    created_at: datetime = Field(..., description="Timestamp when the request was created")


    model_config = ConfigDict(
        populate_by_name=True, # Allows mapping _id to id
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str, 
            # Ensure datetime is serialized correctly to ISO format string
            datetime: lambda dt: dt.isoformat() if isinstance(dt, datetime) else None 
        } 
    )
class EventUpdate(BaseModel):
    event_name: Optional[str] = None
    
    requires_funding: Optional[bool] = None
    estimated_attendees: Optional[int] = None
    requested_date: Optional[date] = None
    requested_time_start: Optional[datetime] = None
    requested_time_end: Optional[datetime] = None
    approval_status: Optional[str] = None
    schedule_id: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 

class EventStatusUpdate(BaseModel):
    """Schema for updating the approval status of an event request."""
    approval_status: EventRequestStatus = Field(..., description="The new status for the event request (Approved or Rejected)")
    admin_comment: Optional[str] = Field(None, max_length=500, description="Reason for status change (especially for rejection)") # <-- Added

    # Optional: Add validator to require comment if status is Rejected
    @field_validator('admin_comment')
    @classmethod
    def comment_required_for_rejection(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        """Requires a comment if the status is being set to Rejected."""
        # info.data contains the data being validated
        if 'approval_status' in info.data and info.data['approval_status'] == EventRequestStatus.REJECTED and not v:
            raise ValueError("An admin comment is required when rejecting an event request.")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "approval_status": "Rejected",
                "admin_comment": "Venue conflict with scheduled maintenance."
            }
        }
    )

# --- Preference Schemas ---
class PreferenceBase(BaseModel):
    """Base schema for event preference properties."""
    event_id: str = Field(..., description="ID of the main event request this preference belongs to")
    preferred_venue_id: Optional[str] = Field(None, description="ID of the alternative preferred venue (optional)")
    # Keep preferred_date as date for input clarity
    preferred_date: Optional[date] = Field(None, description="Alternative preferred date (YYYY-MM-DD, optional)")
    # Use datetime for time slots for consistency with Event schema
    preferred_time_slot_start: Optional[datetime] = Field(None, description="Alternative preferred start time (ISO 8601 format, optional)")
    preferred_time_slot_end: Optional[datetime] = Field(None, description="Alternative preferred end time (ISO 8601 format, optional)")

    # Add validators for ID formats
    @field_validator("event_id", "preferred_venue_id")
    @classmethod
    def validate_objectid_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId format: {v}")
        return v

    # Optional: Add validator to ensure if start time is given, end time is also given and is later
    @field_validator('preferred_time_slot_end')
    @classmethod
    def validate_time_slots(cls, v: Optional[datetime], info: FieldValidationInfo) -> Optional[datetime]:
        start_time = info.data.get('preferred_time_slot_start')
        if start_time and v:
            if v <= start_time:
                raise ValueError("Preferred end time must be after preferred start time")
        elif start_time and not v:
            raise ValueError("Preferred end time is required if start time is provided")
        elif not start_time and v:
             raise ValueError("Preferred start time is required if end time is provided")
        return v

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "event_id": "681293885b447dc3f525bbf3", # ID of the original event request
                "preferred_venue_id": "68129f0c6d0bee76fee415e9", # Example alternative venue ID
                "preferred_date": "2025-11-29",
                "preferred_time_slot_start": "2025-11-29T10:00:00Z", # Use ISO format with timezone
                "preferred_time_slot_end": "2025-11-29T12:00:00Z"
            }
        }
    )

class PreferenceCreate(PreferenceBase):
    """Schema used for creating a new event preference via the API."""
    # Inherits all fields and validation from PreferenceBase
    pass # No additional fields needed for creation specific schema

class PreferenceResponse(PreferenceBase):
    """Schema for returning preference data in API responses."""
    # Use Field alias to map MongoDB's _id to 'id' in the response
    id: str = Field(..., alias="_id", description="Unique ID of the preference record")
    # Include created_at timestamp if you add it to the model/DB
    # created_at: datetime = Field(..., description="Timestamp when the preference was created")

    model_config = ConfigDict(
        populate_by_name=True, # Allows mapping _id to id
        arbitrary_types_allowed=True,
        json_encoders={ # Ensure consistent serialization
            ObjectId: str,
            datetime: lambda dt: dt.isoformat().replace('+00:00', 'Z') if isinstance(dt, datetime) else None,
            date: lambda d: d.isoformat() if isinstance(d, date) else None
        },
         json_schema_extra={
            "example": {
                "_id": "6813a0f5e4b0d6c7e8f9a1b2", # Example generated preference ID
                "event_id": "681293885b447dc3f525bbf3",
                "preferred_venue_id": "68129f0c6d0bee76fee415e9",
                "preferred_date": "2025-11-29",
                "preferred_time_slot_start": "2025-11-29T10:00:00Z",
                "preferred_time_slot_end": "2025-11-29T12:00:00Z"
                # "created_at": "2025-05-01T18:55:00Z" # Example timestamp
            }
        }
    )

class PreferenceUpdate(BaseModel):
    preferred_venue: Optional[str] = None
    preferred_date: Optional[time] = None
    preferred_time_slot_start: Optional[time] = None
    preferred_time_slot_end: Optional[time] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 

# --- Venue Schemas ---
class VenueBase(BaseModel):
    building: str
    venue_type: str
    occupancy: int
    code: str
    availability: str

    model_config = ConfigDict(arbitrary_types_allowed=True) 

class VenueCreate(VenueBase):
    pass

class VenueResponse(VenueBase):
    id: str = Field(..., alias="_id")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

class VenueUpdate(BaseModel):
    building: Optional[str] = None
    venue_type: Optional[str] = None
    occupancy: Optional[int] = None
    code: Optional[str] = None
    availability: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 

# --- Equipment Schemas ---
class EquipmentBase(BaseModel):
    name: str
    availability: str

    model_config = ConfigDict(arbitrary_types_allowed=True) 

class EquipmentResponse(EquipmentBase):
    """Schema for returning equipment data in responses."""
    id: str = Field(..., alias="_id")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

class EquipmentCreate(EquipmentBase):
    pass


class EquipmentUpdate(BaseModel):
    name: Optional[str] = None
    availability: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 



# --- EventEquipment Schemas ---
class EventEquipmentBase(BaseModel):
    event_id: str
    equipment_id: str
    quantity: int = 1
    
    model_config = ConfigDict(arbitrary_types_allowed=True) 

class EventEquipmentCreate(EventEquipmentBase):
    pass

class EventEquipmentResponse(EventEquipmentBase):
    id: str = Field(..., alias="_id")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

class EventEquipmentUpdate(BaseModel):
    quantity: Optional[int] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 