# routers/events.py

import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
import os
import uuid
import json
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form, Body, Path
from typing import List, Optional, Dict, Any # Added Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from bson.errors import InvalidId
# Import datetime, date, time, timezone
from datetime import datetime, date, time, timezone, timedelta

from database import get_database
# --- Import Schemas ---
# Use the updated schemas with datetime fields
from schemas import (
    EventCreate,
    EventResponse,
    UserResponse,
    UserRole,
    RequestedEquipmentItem,
    EventRequestStatus,
    PreferenceCreate,  # <--- Import PreferenceCreate
    PreferenceResponse,
    EventStatusUpdate
)
# --- Import DB Models ---
# Use the updated Event model (without 'id' field) and EventEquipment
from modelsv1 import Event, EventEquipment 
# Import authentication dependency
from auth.auth_handler import get_current_active_user
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
# --- S3 Configuration ---
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION")

s3_client = None
if S3_BUCKET_NAME and AWS_REGION:
    try:
        s3_client = boto3.client('s3', region_name=AWS_REGION)
        s3_client.list_buckets() # Simple check
        print(f"Successfully configured S3 client for bucket {S3_BUCKET_NAME} in region {AWS_REGION}")
    except (NoCredentialsError, PartialCredentialsError):
        print("AWS credentials not found. S3 upload will be disabled.")
        s3_client = None
    except ClientError as e:
        print(f"AWS S3 ClientError during initialization: {e}. S3 upload might be disabled.")
        s3_client = None
    except Exception as e:
        print(f"An unexpected error occurred during S3 client initialization: {e}")
        s3_client = None
else:
    print("S3_BUCKET_NAME or AWS_REGION environment variables not set. S3 upload disabled.")

# Define the router
router = APIRouter(
    prefix="/events"
)

# === Helper Function for S3 Upload ===
async def upload_file_to_s3(file: UploadFile, bucket: str, org_id: str, event_name: str) -> Optional[str]:
    """Uploads a file to S3 and returns the object key, or None if upload fails."""
    if not s3_client or not file or not file.filename:
        return None

    safe_event_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in event_name)
    file_extension = os.path.splitext(file.filename)[1]
    object_key = f"event_requests/{org_id}/{safe_event_name}_{uuid.uuid4().hex}{file_extension}"

    try:
        print(f"Attempting to upload {file.filename} to s3://{bucket}/{object_key}")
        s3_client.upload_fileobj(
            file.file,
            bucket,
            object_key,
            ExtraArgs={'ContentType': file.content_type}
        )
        print(f"Successfully uploaded to {object_key}")
        return object_key
    except ClientError as e:
        print(f"Failed to upload {file.filename} to S3: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during S3 upload: {e}")
        return None

# === Endpoint to Submit an Event Request (Updated) ===
@router.post(
    "/request",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new event request (Students only, optional document upload)"
)
async def submit_event_request(
    request_data_json: str = Form(...),
    document: Optional[UploadFile] = File(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Allows an authenticated student user to submit a new event request.
    Requires sending data as `multipart/form-data`.

    - **request_data**: A JSON string containing the event details 
      (requested_date, requested_time_start, requested_time_end should be ISO 8601 datetime strings).
    - **document**: An optional file (e.g., PDF, DOCX).
    """
    # --- Authorization and User Info Retrieval ---
    user_role = current_user.get("role")
    if user_role != UserRole.STUDENT.value:
        raise HTTPException(status_code=403, detail="Only students can submit event requests.")

    user_org_id = current_user.get("organization") # ObjectId
    if not user_org_id or not isinstance(user_org_id, ObjectId):
         raise HTTPException(status_code=400, detail="Student user not associated with a valid organization.")

    user_id = current_user.get("_id") # ObjectId
    if not user_id or not isinstance(user_id, ObjectId):
         raise HTTPException(status_code=500, detail="Could not identify requesting user.")

    # --- Clean and Parse JSON data from Form field ---
    try:
        cleaned_json_string = request_data_json.strip()
        last_brace_index = cleaned_json_string.rfind('}')
        if last_brace_index == -1:
             raise json.JSONDecodeError("Missing closing '}' in JSON data.", cleaned_json_string, 0)
        json_to_parse = cleaned_json_string[:last_brace_index + 1]
        request_data_dict = json.loads(json_to_parse)
        # Pydantic now expects datetime strings for date/time fields
        request_data = EventCreate.model_validate(request_data_dict)
        print("DEBUG: Successfully parsed and validated request_data")

    except json.JSONDecodeError as json_decode_error:
        print(f"Error decoding JSON string: {json_decode_error}")
        raise HTTPException(status_code=422, detail=f"Invalid JSON format provided: {json_decode_error}")
    except Exception as validation_error:
        print(f"Error validating parsed JSON data: {validation_error}")
        raise HTTPException(status_code=422, detail=f"Invalid event request data structure: {validation_error}")

    # --- ** ADD DUPLICATE CHECK HERE ** ---
    try:
        # Prepare date range for the check (start and end of the requested day in UTC)
        # request_data.requested_date is already a datetime from validation
        requested_day_start_utc = datetime.combine(
            request_data.requested_date.date(), time.min, tzinfo=timezone.utc
        )
        requested_day_end_utc = requested_day_start_utc + timedelta(days=1)

        # Define the query filter
        duplicate_check_filter = {
            "event_name": request_data.event_name,
            "organization_id": user_org_id, # Use the ObjectId of the user's org
            "requested_date": {
                "$gte": requested_day_start_utc,
                "$lt": requested_day_end_utc
            },
            # Optional: Add status check if you only want to prevent duplicates of PENDING/APPROVED events
            # "approval_status": {"$ne": EventRequestStatus.REJECTED.value}
        }

        existing_event = await db.events.find_one(duplicate_check_filter)

        if existing_event:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An event request named '{request_data.event_name}' already exists for this organization on {request_data.requested_date.date().isoformat()}."
            )
        print("DEBUG: No duplicate event found.")

    except HTTPException as http_exc:
         raise http_exc # Re-raise the 409 exception
    except Exception as e:
         print(f"Error during duplicate event check: {e}")
         # Decide if this should be a 500 error or allow proceeding
         raise HTTPException(status_code=500, detail="Error checking for duplicate events.")
    # --- ** END DUPLICATE CHECK ** ---

    # --- Handle File Upload to S3 ---
    document_s3_key: Optional[str] = None
    if document:
        if not s3_client:
             raise HTTPException(status_code=501, detail="File upload is not configured on the server.")
        document_s3_key = await upload_file_to_s3(
            file=document, bucket=S3_BUCKET_NAME, org_id=str(user_org_id), event_name=request_data.event_name
        )
        if not document_s3_key:
             raise HTTPException(status_code=500, detail="Failed to upload supporting document.")

    # --- Prepare Event data for DB ---
    requested_venue_object_id: Optional[ObjectId] = None
    if request_data.requested_venue_id:
        try:
            venue_exists = await db.venues.find_one({"_id": ObjectId(request_data.requested_venue_id)}, {"_id": 1})
            if not venue_exists:
                 raise HTTPException(status_code=404, detail=f"Requested venue ID '{request_data.requested_venue_id}' not found.")
            requested_venue_object_id = ObjectId(request_data.requested_venue_id) 
        except InvalidId:
             raise HTTPException(status_code=422, detail=f"Invalid format for requested_venue_id: {request_data.requested_venue_id}")
        except Exception as e:
             print(f"Error checking venue ID: {e}")
             raise HTTPException(status_code=500, detail="Error validating requested venue.")

    try:
    # Ensure datetime objects are timezone-aware (UTC)
    # Use the validated data directly from request_data (EventCreate instance)
        req_date_utc = request_data.requested_date
        if req_date_utc.tzinfo is None: req_date_utc = req_date_utc.replace(tzinfo=timezone.utc)

        start_time_utc = request_data.requested_time_start
        if start_time_utc.tzinfo is None: start_time_utc = start_time_utc.replace(tzinfo=timezone.utc)

        end_time_utc = request_data.requested_time_end
        if end_time_utc.tzinfo is None: end_time_utc = end_time_utc.replace(tzinfo=timezone.utc)

        # --- CHANGE START ---
        # Directly construct the dictionary for MongoDB insertion
        # using the validated request_data and fetched ObjectIds
        event_dict_to_insert = {
            "event_name": request_data.event_name,
            "description": request_data.description,
            "organization_id": user_org_id, # Use the actual ObjectId
            "requesting_user_id": user_id,   # Use the actual ObjectId
            "requires_funding": request_data.requires_funding,
            "estimated_attendees": request_data.estimated_attendees,
            "requested_date": req_date_utc,
            "requested_time_start": start_time_utc,
            "requested_time_end": end_time_utc,
            "requested_venue_id": requested_venue_object_id, # Use the actual ObjectId (or None)
            "request_document_key": document_s3_key,
            "approval_status": EventRequestStatus.PENDING.value, # Set default status explicitly
            "created_at": datetime.now(timezone.utc)          # Set creation timestamp explicitly
            # Add any other fields with default values needed for the DB document
        }
    # --- CHANGE END ---

        print(f"DEBUG: Dictionary prepared for DB insertion: {event_dict_to_insert}")

    # Convert date object (if present) to datetime object AFTER construction (if necessary - check types)
    # This part might not be needed anymore if using datetimes directly
    # if 'requested_date' in event_dict_to_insert and isinstance(event_dict_to_insert['requested_date'], date):
    #     event_date = event_dict_to_insert['requested_date']
    #     event_dict_to_insert['requested_date'] = datetime.combine(
    #         event_date, time.min, tzinfo=timezone.utc
    #     )
    #     print(f"DEBUG: Converted requested_date to datetime for DB: {event_dict_to_insert['requested_date']}")

    except Exception as data_prep_error:
        # Keep specific error handling if needed, but Pydantic error less likely here
        print(f"Error preparing data for DB insertion: {data_prep_error}")
        raise HTTPException(status_code=422, detail=f"Invalid event request data: {data_prep_error}")

    # --- Insert Event into DB ---
    inserted_event_id: Optional[ObjectId] = None
    try:
        # Insert the dictionary (MongoDB will add _id)
        insert_result = await db.events.insert_one(event_dict_to_insert)
        inserted_event_id = insert_result.inserted_id
        
        # --- Handle Requested Equipment ---
        if request_data.requested_equipment:
            equipment_docs_to_insert = []
            if not inserted_event_id or not isinstance(inserted_event_id, ObjectId):
                 raise ValueError("Failed to get valid ObjectId after event insertion.")
                 
            event_id_str = str(inserted_event_id) 

            equipment_ids_to_validate = {item.equipment_id for item in request_data.requested_equipment}
            valid_equipment_object_ids = {}
            try:
                 object_ids = [ObjectId(eq_id) for eq_id in equipment_ids_to_validate]
                 cursor = db.equipment.find({"_id": {"$in": object_ids}}, {"_id": 1})
                 async for eq_doc in cursor:
                     valid_equipment_object_ids[str(eq_doc["_id"])] = eq_doc["_id"] 
            except InvalidId as e:
                 if inserted_event_id: await db.events.delete_one({"_id": inserted_event_id})
                 raise HTTPException(status_code=422, detail=f"Invalid equipment ID format found in request: {e}")
            except Exception as e:
                 if inserted_event_id: await db.events.delete_one({"_id": inserted_event_id})
                 print(f"Error validating equipment IDs: {e}")
                 raise HTTPException(status_code=500, detail="Error validating requested equipment.")

            for item in request_data.requested_equipment:
                if item.equipment_id not in valid_equipment_object_ids:
                     if inserted_event_id: await db.events.delete_one({"_id": inserted_event_id})
                     raise HTTPException(status_code=404, detail=f"Requested equipment ID '{item.equipment_id}' not found.")
                
                equipment_id_str = str(valid_equipment_object_ids[item.equipment_id])

                # Create EventEquipment model instance using STRINGS for PyObjectId fields
                event_equipment_data = EventEquipment(
                    event_id=event_id_str, 
                    equipment_id=equipment_id_str, 
                    quantity=item.quantity
                )
                # Dump EventEquipment model WITHOUT by_alias
                equipment_docs_to_insert.append(event_equipment_data.model_dump()) 

            if equipment_docs_to_insert:
                # Insert the documents (MongoDB will add _id)
                await db.event_equipment.insert_many(equipment_docs_to_insert)
                print(f"Inserted {len(equipment_docs_to_insert)} equipment links for event {inserted_event_id}")

        # --- Retrieve final document and Prepare Response ---
        created_event_doc = await db.events.find_one({"_id": inserted_event_id}) 
        if not created_event_doc:
             raise HTTPException(status_code=500, detail="Critical error: Failed to retrieve created event immediately after insertion.")

        # --- Explicitly build the response dictionary ---
        response_data: Dict[str, Any] = {}
        for key, value in created_event_doc.items():
            if key == "_id":
                # Map MongoDB '_id' to 'id' field in response schema
                response_data["id"] = str(value) 
            elif isinstance(value, ObjectId):
                # Convert other ObjectIds to strings
                response_data[key] = str(value)
            elif isinstance(value, (datetime, date, time)):
                 # Let Pydantic handle datetime/date/time serialization via schema
                 response_data[key] = value
            elif key == "approval_status" and isinstance(value, str):
                 # Ensure status matches the enum for validation
                 try:
                     response_data[key] = EventRequestStatus(value)
                 except ValueError:
                      print(f"Warning: Invalid status '{value}' found in DB for event {response_data.get('id')}. Setting to PENDING.")
                      response_data[key] = EventRequestStatus.PENDING # Default fallback
            else:
                 response_data[key] = value
        
        # Ensure all required fields for EventResponse are present before validation
        # Example: Check for 'created_at' if it's mandatory in EventResponse
        if "created_at" not in response_data:
             print(f"Warning: 'created_at' missing from retrieved event doc {response_data.get('id')}")
             # Handle appropriately - raise error or provide default if schema requires it
             # response_data["created_at"] = datetime.now(timezone.utc) # Example default

        # Pass the explicitly prepared dictionary to EventResponse
        return EventResponse(**response_data)

    except Exception as e:
        print(f"Error during event creation or equipment linking for user {user_id}: {e}")
        if inserted_event_id and not await db.event_equipment.find_one({"event_id": inserted_event_id}):
             print(f"Rolling back event creation due to equipment linking failure. Deleting event: {inserted_event_id}")
             await db.events.delete_one({"_id": inserted_event_id}) 
        elif inserted_event_id:
             print(f"Potentially orphaned event document created with ID: {inserted_event_id}")
        raise HTTPException(status_code=500, detail=f"Failed to process event request due to an internal server error.")

# === Endpoint to Submit Event Preferences ===
@router.post(
    "/preferences", # Changed path to be more RESTful
    response_model=PreferenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit alternative preferences for an existing event request"
)
async def submit_event_preference(
    preference_data: PreferenceCreate = Body(...), # Use Body for JSON payload
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Allows an authenticated user (typically the event requester or from the same org)
    to submit alternative scheduling preferences for an existing event request.

    - **preference_data**: JSON body containing preference details linked by `event_id`.
    """
    # --- Input Validation (Handled by Pydantic via PreferenceCreate) ---

    # --- Authorization and Event Validation ---
    try:
        event_object_id = ObjectId(preference_data.event_id)
    except InvalidId:
        raise HTTPException(status_code=422, detail=f"Invalid format for event_id: {preference_data.event_id}")

    original_event = await db.events.find_one({"_id": event_object_id})
    if not original_event:
        raise HTTPException(status_code=404, detail=f"Event request with ID '{preference_data.event_id}' not found.")

    user_org_id = current_user.get("organization")
    event_org_id = original_event.get("organization_id")

    if not user_org_id or user_org_id != event_org_id:
        raise HTTPException(status_code=403, detail="You are not authorized to add preferences for this event request.")

    # --- Validate Preferred Venue (if provided) ---
    preferred_venue_object_id: Optional[ObjectId] = None
    if preference_data.preferred_venue_id:
        try:
            preferred_venue_object_id = ObjectId(preference_data.preferred_venue_id)
            venue_exists = await db.venues.find_one({"_id": preferred_venue_object_id}, {"_id": 1})
            if not venue_exists:
                 raise HTTPException(status_code=404, detail=f"Preferred venue ID '{preference_data.preferred_venue_id}' not found.")
        except InvalidId:
             raise HTTPException(status_code=422, detail=f"Invalid format for preferred_venue_id: {preference_data.preferred_venue_id}")
        except HTTPException as http_exc:
             raise http_exc
        except Exception as e:
             print(f"Error checking preferred venue ID: {e}")
             raise HTTPException(status_code=500, detail="Error validating preferred venue.")

    # --- Prepare Preference Data for DB ---
    try:
        pref_date_utc: Optional[datetime] = None
        if preference_data.preferred_date:
            pref_date_utc = datetime.combine(
                preference_data.preferred_date, time.min, tzinfo=timezone.utc
            )

        pref_start_time_utc = preference_data.preferred_time_slot_start
        if pref_start_time_utc and pref_start_time_utc.tzinfo is None:
            pref_start_time_utc = pref_start_time_utc.replace(tzinfo=timezone.utc)

        pref_end_time_utc = preference_data.preferred_time_slot_end
        if pref_end_time_utc and pref_end_time_utc.tzinfo is None:
            pref_end_time_utc = pref_end_time_utc.replace(tzinfo=timezone.utc)

        preference_dict_to_insert = {
            "event_id": event_object_id,
            "preferred_venue_id": preferred_venue_object_id,
            "preferred_date": pref_date_utc,
            "preferred_time_slot_start": pref_start_time_utc,
            "preferred_time_slot_end": pref_end_time_utc,
            "created_at": datetime.now(timezone.utc)
        }
        print(f"DEBUG: Preference dictionary prepared for DB: {preference_dict_to_insert}")

    except Exception as data_prep_error:
        print(f"Error preparing preference data for DB insertion: {data_prep_error}")
        raise HTTPException(status_code=500, detail=f"Internal error preparing preference data.")


    # --- Insert Preference into DB ---
    try:
        insert_result = await db.preferences.insert_one(preference_dict_to_insert)
        inserted_preference_id = insert_result.inserted_id

        created_preference_doc = await db.preferences.find_one({"_id": inserted_preference_id})
        if not created_preference_doc:
             raise HTTPException(status_code=500, detail="Critical error: Failed to retrieve created preference.")

        # --- Prepare and Return Response ---
        # --- FIX START: Manually convert ObjectIds to strings for response validation ---
        response_data_dict: Dict[str, Any] = {}
        for key, value in created_preference_doc.items():
            if key == "_id":
                # Map MongoDB '_id' (ObjectId) to 'id' (str) in response schema
                response_data_dict["id"] = str(value)
            elif isinstance(value, ObjectId):
                # Convert other ObjectIds to strings
                response_data_dict[key] = str(value)
            elif isinstance(value, (datetime, date, time)):
                 # Let Pydantic handle datetime/date/time serialization via schema's json_encoders
                 response_data_dict[key] = value
            else:
                 response_data_dict[key] = value

        # Ensure all required fields for PreferenceResponse are present if needed
        # (e.g., if 'created_at' was mandatory in the schema)
        # if "created_at" not in response_data_dict:
        #      print(f"Warning: 'created_at' missing from retrieved preference doc {response_data_dict.get('id')}")
             # Handle missing fields if necessary

        # Pass the explicitly prepared dictionary with string IDs to PreferenceResponse
        return PreferenceResponse(**response_data_dict)
        # --- FIX END ---
    except Exception as e:
        print(f"Error inserting preference into database: {e}")
        # Consider if rollback is needed (not usually for simple inserts)
        raise HTTPException(status_code=500, detail="Failed to save event preference.")

# === Endpoint to Update Event Request Status (Admin Only) ===
@router.patch(
    "/{event_id}/status",
    response_model=EventResponse,
    status_code=status.HTTP_200_OK,
    summary="Update status, add comment, create schedule on approval" # Updated summary
)
async def update_event_status(
    event_id: str = Path(..., description="The ID of the event request to update"),
    status_update: EventStatusUpdate = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Allows an administrator to update an event request status:
    - Approve: Sets status to Approved, creates a schedule entry, links schedule ID to event.
    - Reject: Sets status to Rejected (requires comment, performs cleanup).
    - Needs Alternatives: Sets status to Needs Alternatives (requires comment, signals GA).

    - **event_id**: ID of the target event request.
    - **status_update**: JSON body containing `approval_status` and optional `admin_comment`.
    """
    # --- Authorization: Check if user is Admin ---
    user_role = current_user.get("role")
    if user_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can update event request status."
        )

    # --- Validate Event ID ---
    try:
        event_object_id = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=422, detail=f"Invalid ObjectId format for event_id: {event_id}")

    # --- Find the event request ---
    # Fetch necessary fields for potential schedule creation
    event_to_update = await db.events.find_one(
        {"_id": event_object_id},
        {"approval_status": 1, "requested_venue_id": 1, "requested_time_start": 1, "requested_time_end": 1, "request_document_key": 1, "admin_comment": 1}
        )
    if not event_to_update:
        raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found.")

    # --- Get new status and comment ---
    current_status = event_to_update.get("approval_status")
    new_status_enum = status_update.approval_status
    new_status_value = new_status_enum.value
    admin_comment = status_update.admin_comment

    # --- Check if status is actually changing ---
    if current_status == new_status_value:
         raise HTTPException(
             status_code=status.HTTP_400_BAD_REQUEST,
             detail=f"Event request is already in the '{new_status_value}' status."
         )

    # --- Specific logic based on new status ---
    perform_cleanup = False
    new_schedule_id: Optional[ObjectId] = None

    if new_status_enum == EventRequestStatus.APPROVED:
        print(f"Event {event_id} set to APPROVED. Attempting to create schedule...")
        admin_comment = None # Clear comment on approval

        # --- Create Schedule Entry ---
        approved_venue_id = event_to_update.get("requested_venue_id")
        approved_start_time = event_to_update.get("requested_time_start")
        approved_end_time = event_to_update.get("requested_time_end")

        # Validate necessary data for schedule
        if not approved_venue_id:
            # Cannot approve without a venue specified in the request
            raise HTTPException(status_code=400, detail="Cannot approve event: Requested venue ID is missing.")
        if not approved_start_time or not approved_end_time:
             # Should not happen if EventCreate validation is correct, but check anyway
             raise HTTPException(status_code=400, detail="Cannot approve event: Requested start or end time is missing.")

        # Ensure times are timezone-aware (should be from initial request)
        if approved_start_time.tzinfo is None: approved_start_time = approved_start_time.replace(tzinfo=timezone.utc)
        if approved_end_time.tzinfo is None: approved_end_time = approved_end_time.replace(tzinfo=timezone.utc)

        # Check for existing schedule for this event (prevent duplicates if re-approved somehow)
        existing_schedule = await db.schedules.find_one({"event_id": event_object_id})
        if existing_schedule:
            print(f"Warning: Schedule already exists for approved event {event_id}. Using existing schedule ID.")
            new_schedule_id = existing_schedule["_id"]
        else:
            # Prepare schedule document
            schedule_dict_to_insert = {
                "event_id": event_object_id,
                "venue_id": approved_venue_id, # Already ObjectId from event doc
                "scheduled_start_time": approved_start_time, # Already datetime from event doc
                "scheduled_end_time": approved_end_time,     # Already datetime from event doc
            }
            try:
                insert_result = await db.schedules.insert_one(schedule_dict_to_insert)
                new_schedule_id = insert_result.inserted_id
                print(f"Successfully created schedule entry {new_schedule_id} for event {event_id}.")
            except Exception as e:
                print(f"Error creating schedule entry for approved event {event_id}: {e}")
                # Critical error: Event status might be updated, but schedule failed.
                # Consider how to handle this inconsistency (e.g., revert status update or log for manual fix)
                raise HTTPException(status_code=500, detail="Failed to create schedule entry for approved event.")

    elif new_status_enum == EventRequestStatus.REJECTED:
        perform_cleanup = True
        print(f"Event {event_id} set to REJECTED. Cleanup will be performed.")

    elif new_status_enum == EventRequestStatus.NEEDS_ALTERNATIVES:
        preference_exists = await db.preferences.find_one({"event_id": event_object_id}, {"_id": 1})
        if not preference_exists:
             raise HTTPException(
                 status_code=status.HTTP_400_BAD_REQUEST,
                 detail="Cannot set status to 'Needs Alternatives': No preferences submitted."
             )
        if not admin_comment:
             print(f"Warning: Setting status to 'Needs Alternatives' for event {event_id} without an admin comment.")
        print(f"Event {event_id} set to NEEDS_ALTERNATIVES. No cleanup performed.")

    # --- Prepare event update data ---
    update_data = {"approval_status": new_status_value}
    if admin_comment is not None:
        update_data["admin_comment"] = admin_comment
    else:
        # Ensure comment is nullified if not provided or cleared during approval
        update_data["admin_comment"] = None

    # Add schedule_id if created
    if new_schedule_id:
        update_data["schedule_id"] = new_schedule_id

    # --- Update the event document in the database ---
    try:
        update_result = await db.events.update_one(
            {"_id": event_object_id},
            {"$set": update_data}
        )
        if update_result.matched_count == 0:
            # This case should be rare if find_one succeeded earlier
            raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found during final update.")

    except Exception as e:
        print(f"Error performing final update on event {event_id}: {e}")
        # If schedule creation succeeded but this fails, there's an inconsistency.
        raise HTTPException(status_code=500, detail="Failed to finalize event update after status change.")

    # --- Perform Cleanup only if explicitly Rejected ---
    if perform_cleanup:
        # ... (Cleanup logic for equipment and S3 as before) ...
        pass # Placeholder for brevity

    # --- Retrieve the fully updated document for response ---
    # Need to fetch the full doc now for the EventResponse model
    updated_event_doc = await db.events.find_one({"_id": event_object_id})
    if not updated_event_doc:
         raise HTTPException(status_code=500, detail="Failed to retrieve event after status update and potential cleanup.")

    # --- Prepare and Return Response ---
    # (Response preparation logic converting ObjectIds etc. remains the same)
    response_data_dict: Dict[str, Any] = {}
    for key, value in updated_event_doc.items():
        if key == "_id":
            response_data_dict["id"] = str(value)
        elif key == "schedule_id" and isinstance(value, ObjectId): # Handle schedule_id
             response_data_dict[key] = str(value)
        elif key == "admin_comment":
             response_data_dict[key] = value
        elif isinstance(value, ObjectId):
            response_data_dict[key] = str(value)
        elif isinstance(value, datetime):
             if value.tzinfo is None:
                 response_data_dict[key] = value.replace(tzinfo=timezone.utc)
             else:
                 response_data_dict[key] = value.astimezone(timezone.utc)
        elif isinstance(value, (date, time)):
             response_data_dict[key] = value
        elif key == "approval_status" and isinstance(value, str):
             try:
                 response_data_dict[key] = EventRequestStatus(value)
             except ValueError:
                  print(f"Warning: Invalid status '{value}' found in DB for event {response_data_dict.get('id')} after update. Defaulting.")
                  response_data_dict[key] = EventRequestStatus.PENDING
        else:
            response_data_dict[key] = value

    # Ensure optional fields exist for validation
    if "admin_comment" not in response_data_dict:
         response_data_dict["admin_comment"] = None
    if "schedule_id" not in response_data_dict:
         response_data_dict["schedule_id"] = None
    # Ensure requested_venue_id is present if needed by EventResponse
    if "requested_venue_id" not in response_data_dict:
         venue_id_val = event_to_update.get("requested_venue_id") # Get from initial fetch
         response_data_dict["requested_venue_id"] = str(venue_id_val) if venue_id_val else None
    # Add other fields required by EventResponse if not fetched initially


    try:
        # Make sure EventResponse schema includes schedule_id: Optional[str]
        return EventResponse(**response_data_dict)
    except Exception as response_error:
         print(f"Error creating response model for updated event {event_id}: {response_error}")
         # Check EventResponse schema includes all fields in response_data_dict
         raise HTTPException(status_code=500, detail="Internal error preparing response after update.")

# --- Add other event-related endpoints below ---
