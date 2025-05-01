# routers/venues.py

from fastapi import APIRouter, HTTPException, Depends, status
from typing import List # Keep for potential future list endpoints
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime, timezone

from database import get_database
# Import venue-specific schemas
from schemas import VenueCreate, VenueResponse 
# Import user schemas/enums needed for auth/RBAC
from schemas import UserResponse, UserRole 
# Import the database model if needed for internal logic (optional here)
# from modelsv1 import Venue, VenueCreateInternal 
# Import authentication dependencies
from auth.auth_handler import get_current_active_user

# Define the router for venue-related endpoints
router = APIRouter(
    prefix="/venues" # Base path for all endpoints in this router
  
)

# --- Role-Based Access Control Dependency (Admin Only) ---
# TODO: Move this dependency to a shared location (e.g., auth/dependencies.py)
async def require_admin(current_user: UserResponse = Depends(get_current_active_user)):
    """
    Dependency that raises an HTTPException if the current user is not an admin.
    Assumes get_current_active_user returns a dict-like object.
    """
    user_role = current_user.get("role")
    if not user_role or user_role != UserRole.ADMIN.value: # Compare with enum's value
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin privileges required."
        )
    return current_user

# === Endpoint to Create a New Venue ===
@router.post(
    "/create", 
    response_model=VenueResponse, 
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)], # Apply admin check
    summary="Create a new venue (Admins only)"
)
async def create_venue(
    venue_data: VenueCreate, # Data from request body validated by VenueCreate schema
    db: AsyncIOMotorDatabase = Depends(get_database) # Database dependency
    # current_user: dict = Depends(require_admin) # Inject admin user if needed later
):
    """
    Allows an authenticated administrator to add a new venue to the system.

    - **building**: Name or code of the building.
    - **venue_type**: Type of venue (e.g., Lecture Hall, Lab, Auditorium).
    - **occupancy**: Maximum capacity.
    - **code**: Unique code for the venue within the building (e.g., Room Number).
    - **availability**: Initial availability status (e.g., "Available", "Under Maintenance"). 
      Consider using an Enum for this later.
    """
    
    # 1. Optional: Check for duplicates (e.g., based on building + code)
    existing_venue = await db.venues.find_one({
        "building": venue_data.building, 
        "code": venue_data.code
    })
    if existing_venue:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Venue with code '{venue_data.code}' already exists in building '{venue_data.building}'."
        )

    # 2. Prepare data for database insertion
    # VenueCreate schema matches the required fields for the Venue model (excluding ID)
    venue_doc = venue_data.model_dump()
    
    # Add any default fields not present in VenueCreate but needed in DB
    # (Based on your models, none seem needed here besides the auto-generated _id)
    # venue_doc["created_at"] = datetime.now(timezone.utc) # Example if needed

    # 3. Insert into database (using "venues" collection)
    try:
        insert_result = await db.venues.insert_one(venue_doc)
        inserted_id = insert_result.inserted_id

        # 4. Retrieve the newly created document to return in the response
        created_venue_doc = await db.venues.find_one({"_id": inserted_id})

        if not created_venue_doc:
             raise HTTPException(status_code=500, detail="Failed to retrieve created venue after insertion.")

        # 5. Prepare and Validate the Response
        # Convert ObjectId to string for the response model
        # VenueResponse uses alias="_id" for the 'id' field
        created_venue_doc["_id"] = str(created_venue_doc["_id"])
        
        # Pass the prepared dictionary to the response model for validation
        return VenueResponse(**created_venue_doc)

    except Exception as e:
        print(f"Error creating venue: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create venue due to an internal error.")

# === Endpoint to List All Venues ===
@router.get(
    "/list",
    response_model=List[VenueResponse], # Return a list of VenueResponse objects
     # Require authentication
    summary="List all available venues"
)
async def get_venue_list(
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> List[VenueResponse]:
    """
    Retrieve a list of all venues currently in the system.
    Requires authentication.
    """
    venues_list = []
    venues_cursor = db.venues.find({}) # Find all documents

    async for venue_doc in venues_cursor:
        try:
            # Convert ObjectId to string before validation
            venue_doc["_id"] = str(venue_doc["_id"])
            # Validate data against the response model
            venues_list.append(VenueResponse(**venue_doc))
        except Exception as e:
            # Log validation errors but continue processing others
            print(f"Error validating venue data for ID {venue_doc.get('_id')}: {e}")
            # Consider skipping this venue or raising a 500 error if strictness is required
            # continue 
            
    return venues_list

# === Endpoint to Get a Specific Venue by ID ===
@router.get(
    "/get/{venue_id}", # Path parameter for the venue ID
    response_model=VenueResponse,
    
    summary="Get details of a specific venue by ID"
)
async def get_venue_by_id(
    # Use Path for validation and extraction of the venue_id from the URL
    venue_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> VenueResponse:
    """
    Retrieve the details of a specific venue by its unique MongoDB ObjectId.
    Requires authentication.
    """
    try:
        # Convert the validated string ID from the path parameter to ObjectId
        venue_object_id = ObjectId(venue_id)
    except InvalidId:
        # This case might be caught by Path regex, but good to have explicit check
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid venue ID format: {venue_id}")

    # Find the venue in the database
    venue_doc = await db.venues.find_one({"_id": venue_object_id})

    # If not found, raise 404 error
    if venue_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Venue with ID {venue_id} not found")

    # Prepare the document for the response model
    try:
        venue_doc["_id"] = str(venue_doc["_id"])
        # Validate the prepared dictionary against the response model
        return VenueResponse(**venue_doc)
    except Exception as e:
        # Catch potential errors during data preparation or Pydantic validation
        print(f"Error preparing response for venue {venue_id}: {e}")
        raise HTTPException(status_code=500, detail="Error processing venue data for response.")

# PUT /venues/{venue_id} (to update a venue - Admin only)
# DELETE /venues/{venue_id} (to delete a venue - Admin only)

