from pydantic import ( # Group imports from the same library
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    ConfigDict,
    FieldValidationInfo # Import FieldValidationInfo here
)
from typing import List, Optional, Any
from datetime import date, time, datetime
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
    members: List[str] # List of member User IDs as strings for response
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
                "created_at": "2023-06-21T12:00:00Z", # Use ISO format for examples
                "updated_at": "2023-06-22T15:30:00Z"
            }
        }
    )

# Helper needed for the cross-field validator in UserCreate
from pydantic import FieldValidationInfo # Import at top if not already there


# --- Schedule Schemas ---
class ScheduleBase(BaseModel):
    venue_id: str
    scheduled_date: time
    scheduled_time_start: time
    scheduled_time_end: time

    model_config = ConfigDict(arbitrary_types_allowed=True) 

class ScheduleCreate(ScheduleBase):
    pass

class ScheduleResponse(ScheduleBase):
    id: str = Field(..., alias="_id")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

class ScheduleUpdate(BaseModel):
    venue_id: Optional[str] = None
    scheduled_date: Optional[time] = None
    scheduled_time_start: Optional[time] = None
    scheduled_time_end: Optional[time] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 

# --- Events Schemas ---
class EventBase(BaseModel):
    event_name: str
    organization_id: str
    requires_funding: bool = False
    estimated_attendees: int = 0
    requested_date: time
    requested_time_start: time
    requested_time_end: time

    model_config = ConfigDict(arbitrary_types_allowed=True) 

class EventCreate(EventBase):
    pass

class EventResponse(EventBase):
    id: str = Field(..., alias="_id")
    approval_status: str
    schedule_id: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

class EventUpdate(BaseModel):
    event_name: Optional[str] = None
    organization_id: Optional[str] = None
    requires_funding: Optional[bool] = None
    estimated_attendees: Optional[int] = None
    requested_date: Optional[time] = None
    requested_time_start: Optional[time] = None
    requested_time_end: Optional[time] = None
    approval_status: Optional[str] = None
    schedule_id: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 

# --- Preference Schemas ---
class PreferenceBase(BaseModel):
    event_id: str
    preferred_venue: Optional[str] = None
    preferred_date: Optional[time] = None
    preferred_time_slot_start: Optional[time] = None
    preferred_time_slot_end: Optional[time] = None

    model_config = ConfigDict(arbitrary_types_allowed=True) 

class PreferenceCreate(PreferenceBase):
    pass

class PreferenceResponse(PreferenceBase):
    event_id: str = Field(..., alias="_id")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

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

class EquipmentCreate(EquipmentBase):
    pass

class EquipmentResponse(EquipmentBase):
    id: str = Field(..., alias="_id")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True) 

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