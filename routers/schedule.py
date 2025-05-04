# routers/schedules.py

from fastapi import APIRouter, HTTPException, Depends, status, Path, Query # Added Path
from typing import List, Optional, Dict, Any # Added Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone, date, time # Ensure all needed types are imported

from database import get_database
from schemas import ScheduleResponse, UserRole # Import specific schemas needed
# Assuming UserResponse is needed for require_admin, adjust if using dict directly
from schemas import UserResponse
from auth.auth_handler import get_current_active_user

# Define the router for schedules-related endpoints
router = APIRouter(
    prefix="/schedules", # Changed prefix to plural for consistency
    tags=["Schedules"] # Tag for API documentation grouping
)

# --- Role-Based Access Control Dependency (Admin Only) ---
# TODO: Move this dependency to a shared location (e.g., auth/dependencies.py)
async def require_admin(current_user: dict = Depends(get_current_active_user)): # Assuming dict return
    """
    Dependency that raises an HTTPException if the current user is not an admin.
    """
    user_role = current_user.get("role")
    if not user_role or user_role != UserRole.ADMIN.value: # Compare with enum's value
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin privileges required."
        )
    return current_user


# --- Helper Function for Processing Schedule Docs (To avoid repetition) ---
def process_schedule_doc(schedule_doc: Dict[str, Any]) -> Optional[ScheduleResponse]:
    """Converts a MongoDB schedule document to a ScheduleResponse object."""
    try:
        response_data_dict: Dict[str, Any] = {}
        for key, value in schedule_doc.items():
            if key == "_id":
                response_data_dict["id"] = str(value)
            # Convert ObjectIds to strings for relevant fields
            elif key in ["event_id", "venue_id", "organization_id"] and isinstance(value, ObjectId):
                response_data_dict[key] = str(value)
            elif isinstance(value, datetime):
                 # Ensure timezone is UTC for consistency
                 if value.tzinfo is None:
                     response_data_dict[key] = value.replace(tzinfo=timezone.utc)
                 else:
                     response_data_dict[key] = value.astimezone(timezone.utc)
            # Include other relevant fields like is_optimized
            elif key in ["is_optimized", "scheduled_start_time", "scheduled_end_time"]:
                 response_data_dict[key] = value
            # Add other fields if necessary, otherwise they are ignored by Pydantic

        # Basic check for required fields before validation attempt
        # Adjust required fields based on ScheduleResponse definition
        required_fields = ["id", "event_id", "venue_id", "scheduled_start_time", "scheduled_end_time"]
        if not all(field in response_data_dict for field in required_fields):
             print(f"Warning: Missing required fields in schedule doc {response_data_dict.get('id')}")
             # Handle missing required fields - skip or raise? Skipping here.
             return None # Indicate failure to process

        # Validate data against the response model
        return ScheduleResponse(**response_data_dict)

    except Exception as e:
        # Log validation errors but allow caller to decide how to handle
        print(f"Error validating/processing schedule data for ID {schedule_doc.get('_id')}: {e}")
        return None # Indicate failure to process


# === MODIFIED Endpoint to Filter Schedules by Date Range (Role-Based) ===
@router.get(
    "/by-range",
    response_model=List[ScheduleResponse],
    summary="List scheduled events within a date range (role-based)"
    # Require authentication for this endpoint
)
async def get_schedules_by_date_range(
    start_date: datetime = Query(..., description="Start date/time (ISO 8601 format)"),
    end_date: datetime = Query(..., description="End date/time (ISO 8601 format)"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user) # REQUIRE Authentication
) -> List[ScheduleResponse]:
    """
    Retrieve schedules within a date range.
    - Admins see all schedules.
    - Students see only schedules for their organization.
    Filters out optimized schedules (is_optimized=False).
    """
    # Ensure dates are UTC
    start_date_utc = start_date.astimezone(timezone.utc) if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
    end_date_utc = end_date.astimezone(timezone.utc) if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)

    # --- Base Query: Date range and NOT optimized ---
    query = {
        "scheduled_start_time": {
            "$gte": start_date_utc,
            "$lt": end_date_utc
        },
        "is_optimized": False # Exclude optimized schedules from this view
    }

    # --- Role-Based Filtering ---
    user_role = current_user.get("role")
    if user_role == UserRole.STUDENT.value:
        user_org_id_str = current_user.get("organization_id") # Ensure key matches user data structure
        if not user_org_id_str:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Student user must belong to an organization."
            )
        try:
            user_org_id = ObjectId(user_org_id_str)
            # Add organization filter for students
            query["organization_id"] = user_org_id
        except InvalidId:
             raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid organization ID format for user: {user_org_id_str}"
            )
        except Exception as e:
             raise HTTPException(status_code=500, detail=f"Error processing user organization: {e}")

    elif user_role != UserRole.ADMIN.value:
         # If role is neither STUDENT nor ADMIN (or missing), deny access
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied for this user role."
        )
    # --- Admins get the base query (all orgs, date range, not optimized) ---

    schedules_list = []
    try:
        schedules_cursor = db.schedules.find(query)
        async for schedule_doc in schedules_cursor:
            processed_schedule = process_schedule_doc(schedule_doc)
            if processed_schedule: # Only append if processing was successful
                schedules_list.append(processed_schedule)

    except Exception as e:
        print(f"Database query error in /schedules/by-range: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching schedules."
        )

    return schedules_list

# === NEW Endpoint to Get Schedules with Event Names ===
@router.post(
    "/with-event-names", # Use POST as it accepts a request body
    response_model=List[ScheduleEventInfoResponseItem],
    summary="Retrieve schedule details including event names based on input list"
    # Add authentication if needed: dependencies=[Depends(get_current_active_user)]
)
async def get_schedules_with_event_names(
    schedule_requests: List[ScheduleEventInfoRequestItem] = Body(...), # Use the new request schema
    db: AsyncIOMotorDatabase = Depends(get_database)
    # current_user: dict = Depends(get_current_active_user) # Uncomment if auth needed
) -> List[ScheduleEventInfoResponseItem]:
    """
    Accepts a list of schedule-like objects, extracts event_ids,
    fetches corresponding event names, and returns the list enriched with event names.
    """
    if not schedule_requests:
        return [] # Return empty list if request is empty

    event_ids_to_fetch = set()
    for req in schedule_requests:
        try:
            # Validate and add event_id to the set
            if ObjectId.is_valid(req.event_id):
                event_ids_to_fetch.add(ObjectId(req.event_id))
            else:
                # Optionally log or handle invalid event_ids in the request
                print(f"Warning: Invalid event_id format '{req.event_id}' in request for schedule '{req._id}'. Skipping.")
        except Exception as e:
             print(f"Error processing event_id {req.event_id} from request item {req._id}: {e}")
             # Decide how to handle: skip this item, raise error? Skipping for now.

    event_names_map: Dict[str, str] = {}
    if event_ids_to_fetch:
        try:
            # Fetch events from DB, projecting only _id and event_name
            events_cursor = db.events.find(
                {"_id": {"$in": list(event_ids_to_fetch)}},
                {"_id": 1, "event_name": 1} # Projection
            )
            async for event_doc in events_cursor:
                event_id_str = str(event_doc["_id"])
                event_name = event_doc.get("event_name")
                if event_name: # Only add if name exists
                    event_names_map[event_id_str] = event_name
        except Exception as e:
            print(f"Database error fetching event names: {e}")
            # Depending on requirements, could raise 500 or proceed without names
            # raise HTTPException(status_code=500, detail="Failed to retrieve event names.")
            pass # Proceeding without names if DB fails

    # Construct the response list
    response_list = []
    for req_item in schedule_requests:
        event_name = event_names_map.get(req_item.event_id) # Get name from map, defaults to None

        # Create response object, copying data from request and adding name
        response_item_data = req_item.model_dump() # Get data from request item
        response_item_data["event_name"] = event_name if event_name else "Event Name Not Found" # Handle missing names

        try:
            # Validate the final structure before appending
            response_obj = ScheduleEventInfoResponseItem(**response_item_data)
            response_list.append(response_obj)
        except Exception as validation_error:
            print(f"Error creating response item for schedule {req_item._id}: {validation_error}")
            # Optionally append a basic dict or skip this item on validation failure

    return response_list

# === NEW Endpoint to Get OPTIMIZED Schedules by Date Range (Admin Only) ===
@router.get(
    "/optimized/by-range",
    response_model=List[ScheduleResponse],
    summary="List OPTIMIZED scheduled events within a date range (Admin Only)",
    dependencies=[Depends(require_admin)] # Secure this endpoint for admins
)
async def get_optimized_schedules_by_range(
    start_date: datetime = Query(..., description="Start date/time (ISO 8601 format)"),
    end_date: datetime = Query(..., description="End date/time (ISO 8601 format)"),
    db: AsyncIOMotorDatabase = Depends(get_database)
    # current_user is implicitly available via require_admin if needed, but not used directly here
) -> List[ScheduleResponse]:
    """
    Retrieve OPTIMIZED schedule entries (e.g., from GA) within a date range.
    Accessible only by administrators.
    """
    # Ensure dates are UTC
    start_date_utc = start_date.astimezone(timezone.utc) if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
    end_date_utc = end_date.astimezone(timezone.utc) if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)

    # --- Query for OPTIMIZED schedules ---
    query = {
        "scheduled_start_time": {
            "$gte": start_date_utc,
            "$lt": end_date_utc
        },
        "is_optimized": True # Query specifically for optimized schedules
        # Add other filters if necessary (e.g., only show latest optimization run)
    }

    schedules_list = []
    try:
        schedules_cursor = db.schedules.find(query)
        async for schedule_doc in schedules_cursor:
             processed_schedule = process_schedule_doc(schedule_doc)
             if processed_schedule:
                 schedules_list.append(processed_schedule)

    except Exception as e:
        print(f"Database query error in /schedules/optimized/by-range: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching optimized schedules."
        )

    return schedules_list

# === Endpoint to List All Schedules ===
@router.get(
    "/list", # Using root path for listing resources is common REST practice
    response_model=List[ScheduleResponse],
    summary="List all scheduled events" # Updated summary
    # Add dependencies=[Depends(require_admin)] if only admins should list all
    # Or dependencies=[Depends(get_current_active_user)] if any authenticated user can list
)
async def get_schedule_list(
    db: AsyncIOMotorDatabase = Depends(get_database)
    # current_user: dict = Depends(get_current_active_user) # Uncomment if auth needed
) -> List[ScheduleResponse]:
    """
    Retrieve a list of all schedule entries currently in the system.
    """
    schedules_list = []
    schedules_cursor = db.schedules.find({}) # Find all documents

    async for schedule_doc in schedules_cursor:
        try:
            # --- Prepare dictionary for response validation ---
            processed_schedule = process_schedule_doc(schedule_doc)
            if processed_schedule: # Only append if processing and validation succeed
                schedules_list.append(processed_schedule)

            # --- End preparation ---
        except Exception as e:
            # Log validation errors but continue processing others
            print(f"Error validating schedule data for ID {schedule_doc.get('_id')}: {e}")
            # Consider skipping this item or raising a 500 error if strictness is required
            # continue

    return schedules_list

# === Endpoint to Get Specific Schedule by ID ===
@router.get(
    "/get/{schedule_id}", # Using root path with ID is common REST practice
    response_model=ScheduleResponse,
    summary="Get details of a specific schedule entry by ID"
    # Add dependencies=[Depends(get_current_active_user)] if auth needed
)
async def get_schedule_by_id(
    schedule_id: str = Path(..., description="The MongoDB ObjectId of the schedule entry"), # Use Path
    db: AsyncIOMotorDatabase = Depends(get_database)
    # current_user: dict = Depends(get_current_active_user) # Uncomment if auth needed
) -> ScheduleResponse:
    """
    Retrieve the details of a specific schedule entry by its unique ID.
    """
    try:
        schedule_object_id = ObjectId(schedule_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid schedule ID format: {schedule_id}")

    # Find the schedule in the database
    schedule_doc = await db.schedules.find_one({"_id": schedule_object_id})

    # If not found, raise 404 error
    if schedule_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Schedule with ID {schedule_id} not found")

    # Prepare the document for the response model
    try:
        # --- Prepare dictionary for response validation ---
        response_data_dict: Dict[str, Any] = {}
        for key, value in schedule_doc.items():
            if key == "_id":
                response_data_dict["id"] = str(value)
            elif key in ["event_id", "venue_id"] and isinstance(value, ObjectId): # Convert other ObjectIds
                response_data_dict[key] = str(value)
            elif isinstance(value, datetime): # Handle datetime
                 if value.tzinfo is None:
                     response_data_dict[key] = value.replace(tzinfo=timezone.utc)
                 else:
                     response_data_dict[key] = value.astimezone(timezone.utc)
            elif isinstance(value, (date, time)): # Handle date/time if used
                 response_data_dict[key] = value
            else:
                response_data_dict[key] = value

        # Ensure required fields for ScheduleResponse are present if needed
        if "event_id" not in response_data_dict:
            print(f"Error: 'event_id' missing from schedule doc {response_data_dict.get('id')}")
            raise HTTPException(status_code=500, detail="Inconsistent schedule data found.") # Raise error if required field missing

        # Validate the prepared dictionary against the response model
        return ScheduleResponse(**response_data_dict)
        # --- End preparation ---
    except Exception as e:
        # Catch potential errors during data preparation or Pydantic validation
        print(f"Error preparing response for schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="Error processing schedule data for response.")

# === Endpoint to Filter Schedules by Date Range ===
@router.get(
    "/by-range", # New endpoint path
    response_model=List[ScheduleResponse],
    summary="List scheduled events within a specific date range"
    # Add dependencies=[Depends(get_current_active_user)] if auth needed by default
)
async def get_schedules_by_date_range(
    start_date: datetime = Query(..., description="Start date/time for the filter range (ISO 8601 format, e.g., 2025-11-01T00:00:00Z)"),
    end_date: datetime = Query(..., description="End date/time for the filter range (ISO 8601 format, e.g., 2025-12-01T00:00:00Z)"),
    db: AsyncIOMotorDatabase = Depends(get_database)
    # current_user: dict = Depends(get_current_active_user) # Uncomment if auth needed
) -> List[ScheduleResponse]:
    """
    Retrieve a list of schedule entries where the `scheduled_start_time`
    falls within the specified date range (inclusive of start, exclusive of end).

    This is useful for populating calendar views.
    """
    # Ensure dates are timezone-aware (UTC is preferred for DB queries)
    if start_date.tzinfo is None:
        start_date_utc = start_date.replace(tzinfo=timezone.utc)
    else:
        start_date_utc = start_date.astimezone(timezone.utc)

    if end_date.tzinfo is None:
        end_date_utc = end_date.replace(tzinfo=timezone.utc)
    else:
        end_date_utc = end_date.astimezone(timezone.utc)

    # --- Construct MongoDB Query ---
    # Find schedules where the start time is within the range
    # $gte: greater than or equal to start_date_utc
    # $lt: less than end_date_utc (this selects events starting *before* the end time)
    query = {
        "scheduled_start_time": {
            "$gte": start_date_utc,
            "$lt": end_date_utc
        }
    }
    # Optional: If you want events that *overlap* the range, the query is more complex:
    # query = {
    #     "$and": [
    #         {"scheduled_start_time": {"$lt": end_date_utc}},
    #         {"scheduled_end_time": {"$gte": start_date_utc}}
    #     ]
    # }
    # Choose the query logic that best suits your calendar's needs.
    # The first query (starts within range) is often sufficient.

    schedules_list = []
    try:
        schedules_cursor = db.schedules.find(query)

        async for schedule_doc in schedules_cursor:
            try:
                # Reuse the conversion logic from your get_schedule_list endpoint
                response_data_dict: Dict[str, Any] = {}
                for key, value in schedule_doc.items():
                    if key == "_id":
                        response_data_dict["id"] = str(value)
                    elif key in ["event_id", "venue_id"] and isinstance(value, ObjectId):
                        response_data_dict[key] = str(value)
                    elif isinstance(value, datetime):
                         if value.tzinfo is None:
                             # Assume UTC if no timezone info (MongoDB default)
                             response_data_dict[key] = value.replace(tzinfo=timezone.utc)
                         else:
                             # Convert to UTC for consistency in response if needed, though schema might handle it
                             response_data_dict[key] = value.astimezone(timezone.utc)
                    elif isinstance(value, (date, time)): # Handle date/time if used
                         response_data_dict[key] = value # Keep as is if they exist
                    else:
                        response_data_dict[key] = value

                # Ensure required fields are present (optional, depends on data integrity)
                if "event_id" not in response_data_dict:
                     print(f"Warning: 'event_id' missing from schedule doc {response_data_dict.get('id')}")
                     continue # Skip this document

                # Validate against the response model before appending
                schedules_list.append(ScheduleResponse(**response_data_dict))

            except Exception as e:
                # Log validation errors for individual documents
                print(f"Error validating schedule data for ID {schedule_doc.get('_id')}: {e}")
                # Depending on strictness, you might want to raise a 500 error here instead of continuing

    except Exception as e:
        # Log errors related to database query itself
        print(f"Database query error in /schedules/by-range: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching schedules."
        )

    return schedules_list
# TODO: Implement other schedule endpoints as needed:
# POST /schedules/ (if manual creation is needed - Admin only?)
# PUT /schedules/{schedule_id} (to update an item - Admin only?)
# DELETE /schedules/{schedule_id} (to delete an item - Admin only?)
