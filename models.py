""" 
SmallVenues = ["pch", "ict","cos","mth"]
LargeVenus = ["OpenField","ULS","OpenCourt"] 
"""


from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional, Literal
from bson import ObjectId
from enum import Enum
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Custom type for MongoDB ObjectId
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(type="string")

# Enum for user roles
class UserRole(str, Enum):
    ADMIN = "admin"
    STUDENT_REP = "student_rep"

# Enum for event status
class EventStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

# User model
class UserBase(BaseModel):
    email: str
    role: UserRole
    organization: Optional[ObjectId] = None

class User(UserBase):
    id: str = Field(alias="_id")
    hashed_password: str

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

# Organization model
class OrganizationBase(BaseModel):
    name: str
    representative: PyObjectId  # Reference to User (student rep)

class Organization(OrganizationBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

# Venue model
class VenueBase(BaseModel):
    name: str
    capacity: int
    equipment: List[str] = []
    location: str  # Physical location, e.g., "Campus Center"
    venue_type: Literal["room", "field","court","auditorium","gym"]

class Venue(VenueBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class UserCreate(BaseModel):
    email: str
    password: str
    role: UserRole
    organization: Optional[str] = None
    
    @validator("email")
    def validate_email_domain(cls, v):
        allowed_domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
        if not v.endswith(allowed_domain):
            raise ValueError(f"You must have an active email in DLSUD to register")
        return v

    @validator("organization")
    def organization_required_for_student_rep(cls, v, values):
        if values.get("role") == UserRole.STUDENT_REP and v is None:
            raise ValueError("Organization is required for student representatives")
        return v

# Event model for creating events
class EventCreate(BaseModel):
    title: str
    description: str
    proposed_start_time: datetime
    proposed_end_time: datetime
    alternative_times: List[dict] = []  # List of alternative start/end times
    venue: PyObjectId  # Reference to Venue
    organization: PyObjectId  # Reference to Organization
    equipment: List[str] = []
    audience: str
    event_type: str
    documents: List[str] = []  # URLs or IDs for related documents
    requires_funding: bool = False  # Indicates if fundraising is needed

# Event model for retrieving events
class Event(EventCreate):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    status: EventStatus = EventStatus.PENDING
    optimized_start_time: Optional[datetime] = None  # Set by genetic algorithm
    optimized_end_time: Optional[datetime] = None  # Set by genetic algorithm

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}

# Optional model for calendar exclusions
class CalendarExclusion(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    date: datetime
    reason: str  # e.g., "Midterms", "Holiday"

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}