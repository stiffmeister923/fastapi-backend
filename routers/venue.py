# routers/venues.py

from fastapi import APIRouter, HTTPException, Depends, status, Path
from typing import List # Keep for potential future list endpoints
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime, timezone

from database import get_database
# Import venue-specific schemas
from schemas import VenueCreate, VenueResponse, VenueUpdate
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

# === Endpoint to Update a Venue ===
@router.put(
    "/update/{venue_id}",
    response_model=VenueResponse,
    dependencies=[Depends(require_admin)], # Apply admin check
    summary="Update an existing venue (Admins only)"
)
async def update_venue(
    update_data: VenueUpdate,
    venue_id: str = Path(..., description="The MongoDB ObjectId of the venue to update"),
     # Data from request body validated by VenueUpdate schema
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Allows an authenticated administrator to update details of an existing venue.
    Only provide the fields you want to change in the request body.
    """
    try:
        venue_object_id = ObjectId(venue_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid venue ID format: {venue_id}")

    # Check if venue exists before trying to update
    existing_venue = await db.venues.find_one({"_id": venue_object_id})
    if not existing_venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Venue with ID {venue_id} not found")

    # Prepare update data: Exclude unset fields to only update provided values
    update_doc = update_data.model_dump(exclude_unset=True)

    # Check if the new code conflicts with another existing venue
    if "code" in update_doc and update_doc["code"] != existing_venue.get("code"):
        code_conflict = await db.venues.find_one(
            {"code": update_doc["code"], "_id": {"$ne": venue_object_id}}
        )
        if code_conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, # Or 409 Conflict
                detail=f"Venue with code '{update_doc['code']}' already exists."
            )

    # Perform the update if there's data to update
    if update_doc:
        try:
            update_result = await db.venues.update_one(
                {"_id": venue_object_id},
                {"$set": update_doc}
            )
            if update_result.matched_count == 0:
                 raise HTTPException(status_code=404, detail=f"Venue with ID {venue_id} disappeared during update.") # Safety check

        except Exception as e:
            print(f"Error updating venue {venue_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to update venue.")
    else:
         raise HTTPException(status_code=400, detail="No update data provided.")


    # Retrieve the updated document to return
    updated_venue_doc = await db.venues.find_one({"_id": venue_object_id})
    if not updated_venue_doc:
         raise HTTPException(status_code=500, detail="Failed to retrieve venue after update.")

    # Prepare and validate the response
    try:
        updated_venue_doc["_id"] = str(updated_venue_doc["_id"])
        return VenueResponse(**updated_venue_doc)
    except Exception as e:
        print(f"Error preparing response for updated venue {venue_id}: {e}")
        raise HTTPException(status_code=500, detail="Error processing updated venue data for response.")


# === Endpoint to Delete a Venue ===
@router.delete(
    "/delete/{venue_id}",
    status_code=status.HTTP_204_NO_CONTENT, # Standard response for successful DELETE
    dependencies=[Depends(require_admin)], # Apply admin check
    summary="Delete a venue (Admins only)"
)
async def delete_venue(
    venue_id: str = Path(..., description="The MongoDB ObjectId of the venue to delete"),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Allows an authenticated administrator to delete an existing venue.
    Returns HTTP 204 No Content on success.

    **Important:** This operation will fail if the venue is currently scheduled
    for any events or is listed as the primary requested venue in any event request.
    """
    try:
        venue_object_id = ObjectId(venue_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid venue ID format: {venue_id}")

    # --- Conflict Check: Prevent deletion if venue is in use ---
    # 1. Check Schedules collection
    scheduled_event = await db.schedules.find_one({"venue_id": venue_object_id})
    if scheduled_event:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete venue ID {venue_id} as it is currently scheduled for event ID {scheduled_event['event_id']}."
        )

    # 2. Check Events collection (for primary requested venue)
    # Only check non-rejected/non-past events if needed, or just check all
    requested_in_event = await db.events.find_one({"requested_venue_id": venue_object_id})
    if requested_in_event:
        # You might want to refine this check based on event status
         raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete venue ID {venue_id} as it is the requested venue for event request ID {requested_in_event['_id']}."
         )

    # 3. Check Preferences collection (optional, might be less critical)
    requested_in_preference = await db.preferences.find_one({"preferred_venue_id": venue_object_id})
    if requested_in_preference:
          raise HTTPException(
             status_code=status.HTTP_409_CONFLICT,
             detail=f"Cannot delete venue ID {venue_id} as it is listed in preferences for event request ID {requested_in_preference['event_id']}."
          )
    # --- End Conflict Check ---


    # Perform the deletion
    try:
        delete_result = await db.venues.delete_one({"_id": venue_object_id})

        if delete_result.deleted_count == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Venue with ID {venue_id} not found.")

        # No response body needed for 204 No Content
        return None

    except HTTPException as http_exc:
         raise http_exc # Re-raise 404 or 409
    except Exception as e:
        print(f"Error deleting venue {venue_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete venue.")

