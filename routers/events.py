# routers/events.py

import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
import os
import uuid
import json
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form, Body, Path, Query
from typing import List, Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, date, time, timezone, timedelta

from database import get_database
# --- Import Schemas ---
# Make sure EventRequestStatus enum in schemas includes CANCELLED
from schemas import (
    EventCreate,
    EventResponse,
    UserResponse,
    UserRole,
    RequestedEquipmentItem,
    EventRequestStatus, # Ensure this includes CANCELLED
    PreferenceCreate,
    PreferenceResponse,
    EventStatusUpdate
)
# --- Import DB Models ---
# Make sure EventRequestStatus enum in modelsv1 includes CANCELLED
from modelsv1 import Event, EventEquipment, EventRequestStatus as ModelEventRequestStatus # Import model enum too
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
    prefix="/events",
    tags=["Events"]
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

# === Helper Function to Fetch and Format Equipment for Response ===
async def _get_formatted_equipment_for_event(event_id: ObjectId, db: AsyncIOMotorDatabase) -> List[RequestedEquipmentItem]:
    """Fetches linked equipment from DB and formats it for the response."""
    equipment_list = []
    equipment_cursor = db.event_equipment.find({"event_id": event_id})
    async for eq_link in equipment_cursor:
        try:
            item = RequestedEquipmentItem(
                equipment_id=str(eq_link["equipment_id"]),
                quantity=eq_link["quantity"]
            )
            equipment_list.append(item)
        except Exception as e:
            print(f"Error formatting equipment link data for event {event_id}, link {eq_link.get('_id')}: {e}")
            continue
    return equipment_list

# === Helper Function for Event Cleanup (Rejection/Cancellation) ===
async def _perform_event_cleanup(event_id: ObjectId, event_doc: Dict[str, Any], db: AsyncIOMotorDatabase, delete_schedule: bool = True):
    """
    Performs cleanup tasks for a rejected or cancelled event.
    Args:
        event_id: The ObjectId of the event.
        event_doc: The event document (fetched before status change).
        db: The database instance.
        delete_schedule: Whether to delete the associated schedule (True for Admin Cancel/Reject, False for Student Cancel of Pending).
    """
    print(f"Performing cleanup for event {event_id}...")
    org_id = event_doc.get("organization_id")
    schedule_id = event_doc.get("schedule_id")
    s3_key = event_doc.get("request_document_key")

    # 1. Remove event from organization's list
    if org_id:
        try:
            await db.organizations.update_one(
                {"_id": org_id},
                {"$pull": {"events": event_id}}
            )
            print(f"Removed event {event_id} from organization {org_id}'s list.")
        except Exception as org_pull_error:
             print(f"Warning: Failed to remove event {event_id} from organization {org_id}: {org_pull_error}")
    else:
        print(f"Warning: Cannot remove event {event_id} from organization list: Organization ID missing from event.")

    # 2. Delete associated schedule (if applicable)
    if delete_schedule and schedule_id:
        try:
            await db.schedules.delete_one({"_id": schedule_id})
            print(f"Deleted schedule {schedule_id} for event {event_id}")
        except Exception as schedule_delete_error:
            print(f"Warning: Failed to delete schedule {schedule_id} for event {event_id}: {schedule_delete_error}")

    # 3. Delete linked equipment entries
    try:
        deleted_eq_count = await db.event_equipment.delete_many({"event_id": event_id})
        print(f"Deleted {deleted_eq_count.deleted_count} equipment links for event {event_id}")
    except Exception as eq_delete_error:
        print(f"Warning: Failed to delete equipment links for event {event_id}: {eq_delete_error}")

    # 4. Delete preferences
    try:
        deleted_pref_count = await db.preferences.delete_many({"event_id": event_id})
        print(f"Deleted {deleted_pref_count.deleted_count} preferences for event {event_id}")
    except Exception as pref_delete_error:
        print(f"Warning: Failed to delete preferences for event {event_id}: {pref_delete_error}")

    # 5. Delete S3 document if it exists
    if s3_key and s3_client and S3_BUCKET_NAME:
        try:
            print(f"Deleting S3 object {s3_key} for event {event_id}")
            s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        except ClientError as s3_error:
            print(f"Warning: Failed to delete S3 object {s3_key}: {s3_error}")
        except Exception as s3_gen_error:
             print(f"Warning: Unexpected error deleting S3 object {s3_key}: {s3_gen_error}")

# === Endpoint: List Pending Event Requests ===
@router.get(
    "/pending",
    response_model=List[EventResponse],
    summary="List pending requests (Admin: advised orgs, Student: own org)" # UPDATED Summary
)
async def list_pending_event_requests(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Retrieves a list of event requests with 'Pending' status.
    - Administrators see pending requests from organizations they advise.
    - Students see only pending requests from their own organization.
    """
    user_role = current_user.get("role")
    user_org_id = current_user.get("organization") # ObjectId or None
    admin_email = current_user.get("email") # EmailStr or None

    query: Dict[str, Any] = {"approval_status": EventRequestStatus.PENDING.value}
    org_ids_to_query: Optional[List[ObjectId]] = None # Used for admin filtering

    if user_role == UserRole.ADMIN.value:
        if not admin_email:
            print(f"Warning: Admin user {current_user.get('_id')} has no email.")
            return [] # Cannot find advised orgs without email

        # Find organizations where this admin is the faculty advisor
        org_cursor = db.organizations.find(
            {"faculty_advisor_email": admin_email}, # Filter organizations by advisor email
            {"_id": 1} # Project only the ID
        )
        org_ids_to_query = [org['_id'] async for org in org_cursor]

        if not org_ids_to_query:
            # No organizations found advised by this admin
            return [] # Return empty list

        # Add organization filter to the main query
        query["organization_id"] = {"$in": org_ids_to_query}

    elif user_role == UserRole.STUDENT.value:
        if not user_org_id:
            print(f"Warning: Student user {current_user.get('email')} has no organization_id.")
            return []
        query["organization_id"] = user_org_id # Filter by student's specific org ObjectId

    else:
        raise HTTPException(status_code=403, detail="Access denied for this user role.")

    # --- Execute Query and Prepare Response ---
    pending_events = []
    try:
        cursor = db.events.find(query).sort("created_at", 1) # Optional: sort by creation time
        async for event_doc in cursor:
            try:
                response_dict = await _prepare_event_response_dict(event_doc, db)
                pending_events.append(EventResponse(**response_dict))
            except ValueError as prep_error: print(f"Error preparing response dict for event {event_doc.get('_id')}: {prep_error}")
            except Exception as validation_error: print(f"Error validating EventResponse for event {event_doc.get('_id')}: {validation_error}")
    except Exception as db_error:
        print(f"Database error fetching pending events: {db_error}")
        raise HTTPException(status_code=500, detail="Failed to retrieve pending event requests.")

    return pending_events

# === Endpoint: List All Relevant Event Requests ===
@router.get(
    "/list",
    response_model=List[EventResponse],
    summary="List all requests (Admin: advised orgs, Student: own org)" # UPDATED Summary
)
async def list_relevant_event_requests(
    status: Optional[List[EventRequestStatus]] = Query(None, description="Filter events by status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Retrieves a list of all event requests relevant to the user.
    - Administrators see requests from organizations they advise.
    - Students see only requests from their own organization.
    - Allows optional filtering by one or more statuses.
    """
    user_role = current_user.get("role")
    user_org_id = current_user.get("organization") # ObjectId or None
    admin_email = current_user.get("email") # EmailStr or None

    query: Dict[str, Any] = {} # Start with an empty query
    org_ids_to_query: Optional[List[ObjectId]] = None # Used for admin filtering

    # --- Role-Based Filtering (Advisor Email for Admin) ---
    if user_role == UserRole.ADMIN.value:
        if not admin_email:
            print(f"Warning: Admin user {current_user.get('_id')} has no email.")
            return [] # Cannot find advised orgs without email

        # Find organizations where this admin is the faculty advisor
        org_cursor = db.organizations.find(
            {"faculty_advisor_email": admin_email}, # Filter organizations by advisor email
            {"_id": 1} # Project only the ID
        )
        org_ids_to_query = [org['_id'] async for org in org_cursor]

        if not org_ids_to_query:
            # No organizations found advised by this admin
            return [] # Return empty list

        # Add organization filter to the main query
        query["organization_id"] = {"$in": org_ids_to_query}

    elif user_role == UserRole.STUDENT.value:
        if not user_org_id:
            print(f"Warning: Student user {current_user.get('email')} has no organization_id.")
            return []
        query["organization_id"] = user_org_id # Filter by student's specific org ObjectId

    else:
        raise HTTPException(status_code=403, detail="Access denied for this user role.")

    # --- Optional Status Filtering ---
    if status:
        status_values = [s.value for s in status]
        query["approval_status"] = {"$in": status_values}

    # --- Execute Query and Prepare Response ---
    relevant_events = []
    try:
        cursor = db.events.find(query).sort("created_at", -1) # Sort by most recent first
        async for event_doc in cursor:
            try:
                response_dict = await _prepare_event_response_dict(event_doc, db)
                relevant_events.append(EventResponse(**response_dict))
            except ValueError as prep_error: print(f"Error preparing response dict for event {event_doc.get('_id')}: {prep_error}")
            except Exception as validation_error: print(f"Error validating EventResponse for event {event_doc.get('_id')}: {validation_error}")
    except Exception as db_error:
        print(f"Database error fetching relevant events: {db_error}")
        raise HTTPException(status_code=500, detail="Failed to retrieve relevant event requests.")

    return relevant_events

# === Helper Function to Prepare Event Response Dictionary ===
async def _prepare_event_response_dict(event_doc: Dict[str, Any], db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    # ... (implementation as before) ...
    if not event_doc or "_id" not in event_doc: raise ValueError("Invalid event document provided.")
    event_id = event_doc["_id"]
    formatted_equipment = await _get_formatted_equipment_for_event(event_id, db)
    response_data: Dict[str, Any] = {}
    for key, value in event_doc.items():
        if key == "_id": response_data["id"] = str(value)
        elif isinstance(value, ObjectId): response_data[key] = str(value)
        elif isinstance(value, (datetime, date, time)): response_data[key] = value
        elif key == "approval_status" and isinstance(value, str):
             try: response_data[key] = EventRequestStatus(value)
             except ValueError: response_data[key] = EventRequestStatus.PENDING
        else: response_data[key] = value
    response_data["requested_equipment"] = formatted_equipment
    for field in EventResponse.model_fields:
        if field not in response_data and field != 'id': response_data[field] = None
    return response_data


# === Endpoint to Submit an Event Request ===
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
    # ... (Authorization, Parsing, Duplicate Check, S3 Upload, Venue Validation logic remains the same) ...
    # --- Authorization and User Info Retrieval ---
    user_role = current_user.get("role")
    if user_role != UserRole.STUDENT.value:
        raise HTTPException(status_code=403, detail="Only students can submit event requests.")

    user_org_id = current_user.get("organization") # Should be ObjectId from token/DB
    if not user_org_id or not isinstance(user_org_id, ObjectId):
         raise HTTPException(status_code=400, detail="Student user not associated with a valid organization.")

    user_id = current_user.get("_id") # Should be ObjectId from token/DB
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
        request_data = EventCreate.model_validate(request_data_dict)
        print("DEBUG: Successfully parsed and validated request_data")

    except json.JSONDecodeError as json_decode_error:
        print(f"Error decoding JSON string: {json_decode_error}")
        raise HTTPException(status_code=422, detail=f"Invalid JSON format provided: {json_decode_error}")
    except Exception as validation_error:
        print(f"Error validating parsed JSON data: {validation_error}")
        raise HTTPException(status_code=422, detail=f"Invalid event request data structure: {validation_error}")

    # --- Duplicate Check ---
    try:
        requested_day_start_utc = datetime.combine(
            request_data.requested_date.date(), time.min, tzinfo=timezone.utc
        )
        requested_day_end_utc = requested_day_start_utc + timedelta(days=1)

        duplicate_check_filter = {
            "event_name": request_data.event_name,
            "organization_id": user_org_id,
            "requested_date": { "$gte": requested_day_start_utc, "$lt": requested_day_end_utc },
            # Prevent creating duplicates if one already exists and isn't rejected/cancelled
            "approval_status": {"$nin": [EventRequestStatus.REJECTED.value, EventRequestStatus.CANCELLED.value]}
        }
        existing_event = await db.events.find_one(duplicate_check_filter)
        if existing_event:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active event request named '{request_data.event_name}' already exists for this organization on {request_data.requested_date.date().isoformat()}."
            )
        print("DEBUG: No duplicate event found.")
    except HTTPException as http_exc:
         raise http_exc
    except Exception as e:
         print(f"Error during duplicate event check: {e}")
         raise HTTPException(status_code=500, detail="Error checking for duplicate events.")

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
        req_date_utc = request_data.requested_date
        if req_date_utc.tzinfo is None: req_date_utc = req_date_utc.replace(tzinfo=timezone.utc)
        start_time_utc = request_data.requested_time_start
        if start_time_utc.tzinfo is None: start_time_utc = start_time_utc.replace(tzinfo=timezone.utc)
        end_time_utc = request_data.requested_time_end
        if end_time_utc.tzinfo is None: end_time_utc = end_time_utc.replace(tzinfo=timezone.utc)

        event_dict_to_insert = {
            "event_name": request_data.event_name,
            "description": request_data.description,
            "organization_id": user_org_id,
            "requesting_user_id": user_id,
            "requires_funding": request_data.requires_funding,
            "estimated_attendees": request_data.estimated_attendees,
            "requested_date": req_date_utc,
            "requested_time_start": start_time_utc,
            "requested_time_end": end_time_utc,
            "requested_venue_id": requested_venue_object_id,
            "request_document_key": document_s3_key,
            "approval_status": EventRequestStatus.PENDING.value,
            "created_at": datetime.now(timezone.utc)
        }
        print(f"DEBUG: Dictionary prepared for DB insertion: {event_dict_to_insert}")

    except Exception as data_prep_error:
        print(f"Error preparing data for DB insertion: {data_prep_error}")
        raise HTTPException(status_code=422, detail=f"Invalid event request data: {data_prep_error}")

    # --- Insert Event into DB ---
    inserted_event_id: Optional[ObjectId] = None
    try:
        insert_result = await db.events.insert_one(event_dict_to_insert)
        inserted_event_id = insert_result.inserted_id
        if not inserted_event_id or not isinstance(inserted_event_id, ObjectId):
             raise ValueError("Failed to get valid ObjectId after event insertion.")

        # Link event to organization
        try:
            await db.organizations.update_one(
                {"_id": user_org_id}, {"$addToSet": {"events": inserted_event_id}}
            )
            print(f"Successfully linked event {inserted_event_id} to organization {user_org_id}.")
        except Exception as org_update_error:
            print(f"Error updating organization {user_org_id} with event {inserted_event_id}: {org_update_error}")

        # Handle Requested Equipment
        if request_data.requested_equipment:
            # ... (Equipment linking logic remains the same) ...
            equipment_docs_to_insert = []
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
                # *** FIX: Convert IDs to strings BEFORE passing to EventEquipment model ***
                event_id_str_for_model = str(inserted_event_id)
                equipment_id_str_for_model = str(valid_equipment_object_ids[item.equipment_id])

                # Create EventEquipment model instance using STRINGS
                # The PyObjectId validator will convert these back to ObjectId internally
                event_equipment_data = EventEquipment(
                    event_id=event_id_str_for_model,
                    equipment_id=equipment_id_str_for_model,
                    quantity=item.quantity
                )
                equipment_docs_to_insert.append(event_equipment_data.model_dump(by_alias=True))

            if equipment_docs_to_insert:
                await db.event_equipment.insert_many(equipment_docs_to_insert)
                print(f"Inserted {len(equipment_docs_to_insert)} equipment links for event {inserted_event_id}")


        # Retrieve final document and Prepare Response
        created_event_doc = await db.events.find_one({"_id": inserted_event_id})
        if not created_event_doc:
             raise HTTPException(status_code=500, detail="Critical error: Failed to retrieve created event immediately after insertion.")
        formatted_equipment = await _get_formatted_equipment_for_event(inserted_event_id, db)

        # Build response dictionary
        response_data: Dict[str, Any] = {}
        # ... (Logic to build response_data remains the same) ...
        for key, value in created_event_doc.items():
            if key == "_id":
                response_data["id"] = str(value)
            elif isinstance(value, ObjectId):
                response_data[key] = str(value)
            elif isinstance(value, (datetime, date, time)):
                 response_data[key] = value
            elif key == "approval_status" and isinstance(value, str):
                 try:
                     response_data[key] = EventRequestStatus(value)
                 except ValueError:
                      response_data[key] = EventRequestStatus.PENDING
            else:
                 response_data[key] = value
        response_data["requested_equipment"] = formatted_equipment

        return EventResponse(**response_data)

    except Exception as e:
        print(f"Error during event creation or linking for user {user_id}: {e}")
        # Rollback logic
        if inserted_event_id:
            print(f"Attempting rollback. Deleting event: {inserted_event_id}")
            await db.events.delete_one({"_id": inserted_event_id})
            # Also remove from org list if linking succeeded
            await db.organizations.update_one({"_id": user_org_id}, {"$pull": {"events": inserted_event_id}})
            # Delete linked equipment
            await db.event_equipment.delete_many({"event_id": inserted_event_id})
        raise HTTPException(status_code=500, detail=f"Failed to process event request due to an internal server error.")


# === Endpoint to Submit Event Preferences ===
@router.post(
    "/preferences",
    response_model=PreferenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit alternative preferences for an existing event request"
)
async def submit_event_preference(
    preference_data: PreferenceCreate = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    # ... (Existing preference submission logic remains the same) ...
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

    # Validate Preferred Venue
    preferred_venue_object_id: Optional[ObjectId] = None
    if preference_data.preferred_venue_id:
        try:
            preferred_venue_object_id = ObjectId(preference_data.preferred_venue_id)
            venue_exists = await db.venues.find_one({"_id": preferred_venue_object_id}, {"_id": 1})
            if not venue_exists:
                 raise HTTPException(status_code=404, detail=f"Preferred venue ID '{preference_data.preferred_venue_id}' not found.")
        except InvalidId:
             raise HTTPException(status_code=422, detail=f"Invalid format for preferred_venue_id: {preference_data.preferred_venue_id}")
        except Exception as e:
             raise HTTPException(status_code=500, detail="Error validating preferred venue.")

    # Prepare Preference Data
    try:
        pref_date_utc: Optional[datetime] = None
        if preference_data.preferred_date:
            pref_date_utc = datetime.combine(preference_data.preferred_date, time.min, tzinfo=timezone.utc)

        pref_start_time_utc = preference_data.preferred_time_slot_start
        if pref_start_time_utc and pref_start_time_utc.tzinfo is None: pref_start_time_utc = pref_start_time_utc.replace(tzinfo=timezone.utc)
        pref_end_time_utc = preference_data.preferred_time_slot_end
        if pref_end_time_utc and pref_end_time_utc.tzinfo is None: pref_end_time_utc = pref_end_time_utc.replace(tzinfo=timezone.utc)

        preference_dict_to_insert = {
            "event_id": event_object_id,
            "preferred_venue_id": preferred_venue_object_id,
            "preferred_date": pref_date_utc,
            "preferred_time_slot_start": pref_start_time_utc,
            "preferred_time_slot_end": pref_end_time_utc,
            "created_at": datetime.now(timezone.utc)
        }
    except Exception as data_prep_error:
        raise HTTPException(status_code=500, detail=f"Internal error preparing preference data.")

    # Insert Preference
    try:
        insert_result = await db.preferences.insert_one(preference_dict_to_insert)
        inserted_preference_id = insert_result.inserted_id
        created_preference_doc = await db.preferences.find_one({"_id": inserted_preference_id})
        if not created_preference_doc:
             raise HTTPException(status_code=500, detail="Critical error: Failed to retrieve created preference.")

        # Prepare Response
        response_data_dict: Dict[str, Any] = {}
        for key, value in created_preference_doc.items():
            if key == "_id": response_data_dict["id"] = str(value)
            elif isinstance(value, ObjectId): response_data_dict[key] = str(value)
            else: response_data_dict[key] = value
        return PreferenceResponse(**response_data_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to save event preference.")


# === Endpoint to Update Event Request Status (Admin Only) ===
@router.patch(
    "/update/{event_id}/status",
    response_model=EventResponse,
    status_code=status.HTTP_200_OK,
    summary="Update status, add comment, create schedule on approval"
)
async def update_event_status(
    event_id: str = Path(..., description="The ID of the event request to update"),
    status_update: EventStatusUpdate = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    # ... (Authorization, ID Validation logic remains the same) ...
    user_role = current_user.get("role")
    if user_role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Only administrators can update event request status.")
    try:
        event_object_id = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=422, detail=f"Invalid ObjectId format for event_id: {event_id}")

    # Fetch event, including fields needed for cleanup/response
    event_to_update = await db.events.find_one(
        {"_id": event_object_id},
        { # Projection
            "approval_status": 1, "requested_venue_id": 1, "requested_time_start": 1,
            "requested_time_end": 1, "request_document_key": 1, "admin_comment": 1,
            "organization_id": 1, "event_name": 1, "description": 1,
            "requesting_user_id": 1, "requires_funding": 1, "estimated_attendees": 1,
            "requested_date": 1, "created_at": 1, "schedule_id": 1
        }
    )
    if not event_to_update:
        raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found.")

    current_status = event_to_update.get("approval_status")
    new_status_enum = status_update.approval_status
    new_status_value = new_status_enum.value
    admin_comment = status_update.admin_comment

    if current_status == new_status_value:
         raise HTTPException(status_code=400, detail=f"Event request is already in the '{new_status_value}' status.")
    # Prevent changing status if already rejected or cancelled
    if current_status in [EventRequestStatus.REJECTED.value, EventRequestStatus.CANCELLED.value]:
         raise HTTPException(status_code=400, detail=f"Cannot change status of a {current_status} event.")

    # --- Specific logic based on new status ---
    perform_full_cleanup = False
    new_schedule_id: Optional[ObjectId] = None
    user_org_id = event_to_update.get("organization_id")

    if new_status_enum == EventRequestStatus.APPROVED:
        # ... (Schedule creation logic remains the same) ...
        print(f"Event {event_id} set to APPROVED. Attempting to create schedule...")
        admin_comment = None # Clear comment on approval
        approved_venue_id = event_to_update.get("requested_venue_id")
        approved_start_time = event_to_update.get("requested_time_start")
        approved_end_time = event_to_update.get("requested_time_end")
        if not approved_venue_id: raise HTTPException(status_code=400, detail="Cannot approve event: Requested venue ID is missing.")
        if not approved_start_time or not approved_end_time: raise HTTPException(status_code=400, detail="Cannot approve event: Requested start or end time is missing.")
        if not user_org_id: raise HTTPException(status_code=500, detail="Cannot create schedule: Event is missing organization ID.")
        if approved_start_time.tzinfo is None: approved_start_time = approved_start_time.replace(tzinfo=timezone.utc)
        if approved_end_time.tzinfo is None: approved_end_time = approved_end_time.replace(tzinfo=timezone.utc)
        existing_schedule = await db.schedules.find_one({"event_id": event_object_id})
        if existing_schedule:
            new_schedule_id = existing_schedule["_id"]
        else:
            schedule_dict_to_insert = { "event_id": event_object_id, "venue_id": approved_venue_id, "organization_id": user_org_id, "scheduled_start_time": approved_start_time, "scheduled_end_time": approved_end_time, "is_optimized": False }
            try:
                insert_result = await db.schedules.insert_one(schedule_dict_to_insert)
                new_schedule_id = insert_result.inserted_id
            except Exception as e: raise HTTPException(status_code=500, detail="Failed to create schedule entry for approved event.")

    elif new_status_enum == EventRequestStatus.REJECTED:
        perform_full_cleanup = True
        print(f"Event {event_id} set to REJECTED. Full cleanup will be performed.")

    elif new_status_enum == EventRequestStatus.NEEDS_ALTERNATIVES:
        # ... (Needs alternatives logic remains the same) ...
        preference_exists = await db.preferences.find_one({"event_id": event_object_id}, {"_id": 1})
        if not preference_exists: raise HTTPException(status_code=400, detail="Cannot set status to 'Needs Alternatives': No preferences submitted.")
        if not admin_comment: print(f"Warning: Setting status to 'Needs Alternatives' for event {event_id} without an admin comment.")

    # --- Prepare event update data ---
    update_data = {"approval_status": new_status_value}
    if admin_comment is not None: update_data["admin_comment"] = admin_comment
    else: update_data["admin_comment"] = None
    if new_schedule_id: update_data["schedule_id"] = new_schedule_id

    # --- Update the event document ---
    try:
        update_result = await db.events.update_one({"_id": event_object_id}, {"$set": update_data})
        if update_result.matched_count == 0: raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found during final update.")
    except Exception as e: raise HTTPException(status_code=500, detail="Failed to finalize event update after status change.")

    # --- Perform Cleanup if Rejected ---
    if perform_full_cleanup:
        await _perform_event_cleanup(event_object_id, event_to_update, db, delete_schedule=True)

    # --- Retrieve final document and Prepare Response ---
    updated_event_doc = await db.events.find_one({"_id": event_object_id})
    if not updated_event_doc: raise HTTPException(status_code=500, detail="Failed to retrieve event after status update.")
    formatted_equipment = await _get_formatted_equipment_for_event(event_object_id, db)
    response_data_dict: Dict[str, Any] = {}
    # ... (Logic to build response_data_dict remains the same) ...
    for key in EventResponse.model_fields.keys():
         if key == "id": response_data_dict["id"] = str(updated_event_doc.get("_id"))
         elif key == "requested_equipment": response_data_dict[key] = formatted_equipment
         elif key in updated_event_doc:
             value = updated_event_doc[key]
             if isinstance(value, ObjectId): response_data_dict[key] = str(value)
             elif isinstance(value, datetime): response_data_dict[key] = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
             elif key == "approval_status" and isinstance(value, str):
                 try: response_data_dict[key] = EventRequestStatus(value)
                 except ValueError: response_data_dict[key] = EventRequestStatus.PENDING
             else: response_data_dict[key] = value
         else: response_data_dict[key] = None
    try:
        return EventResponse(**response_data_dict)
    except Exception as response_error:
         print(f"Error creating response model for updated event {event_id}: {response_error}")
         raise HTTPException(status_code=500, detail="Internal error preparing response after update.")


# === NEW Endpoint: Student Cancel Pending Event ===
@router.patch(
    "/{event_id}/cancel-request",
    status_code=status.HTTP_204_NO_CONTENT, # Return No Content on successful cancellation
    summary="Cancel a pending event request (Students only)"
)
async def cancel_pending_event_request(
    event_id: str = Path(..., description="The ID of the event request to cancel"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Allows the student who requested the event (or another student from the same org)
    to cancel it, **only if it is currently in 'Pending' status**.
    This performs cleanup (removes links, deletes preferences/files) but does NOT delete schedules.
    """
    # --- Authorization: Check if user is Student ---
    user_role = current_user.get("role")
    if user_role != UserRole.STUDENT.value:
        raise HTTPException(status_code=403, detail="Only students can cancel event requests.")

    # --- Validate Event ID ---
    try:
        event_object_id = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=422, detail=f"Invalid ObjectId format for event_id: {event_id}")

    # --- Find the event and verify ownership/status ---
    event_to_cancel = await db.events.find_one(
        {"_id": event_object_id},
        {"approval_status": 1, "organization_id": 1, "schedule_id": 1, "request_document_key": 1} # Fetch fields needed for checks/cleanup
    )
    if not event_to_cancel:
        raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found.")

    # Check ownership (user belongs to the event's organization)
    user_org_id = current_user.get("organization")
    event_org_id = event_to_cancel.get("organization_id")
    if not user_org_id or user_org_id != event_org_id:
        raise HTTPException(status_code=403, detail="You are not authorized to cancel this event request.")

    # Check if status is PENDING
    current_status = event_to_cancel.get("approval_status")
    if current_status != EventRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel event: Event status is '{current_status}', not 'Pending'."
        )

    # --- Update Event Status to Cancelled ---
    try:
        update_result = await db.events.update_one(
            {"_id": event_object_id},
            {"$set": {"approval_status": EventRequestStatus.CANCELLED.value}}
        )
        if update_result.matched_count == 0:
            # Should not happen if find_one succeeded, but safety check
            raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found during cancellation update.")
        print(f"Event {event_id} status updated to Cancelled by student.")
    except Exception as e:
        print(f"Error updating event {event_id} status to Cancelled: {e}")
        raise HTTPException(status_code=500, detail="Failed to update event status during cancellation.")

    # --- Perform Cleanup (No Schedule Deletion for Student Cancel) ---
    await _perform_event_cleanup(event_object_id, event_to_cancel, db, delete_schedule=False)

    # --- Return No Content ---
    return None # FastAPI handles the 204 response


# === NEW Endpoint: Admin Cancel Any Event ===
@router.patch(
    "/{event_id}/admin-cancel",
    status_code=status.HTTP_204_NO_CONTENT, # Return No Content on successful cancellation
    summary="Cancel any event request (Admins only)"
)
async def admin_cancel_event_request(
    event_id: str = Path(..., description="The ID of the event request to cancel"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Allows an administrator to cancel any event request, regardless of its current status.
    This performs full cleanup including deleting any associated schedule.
    """
    # --- Authorization: Check if user is Admin ---
    user_role = current_user.get("role")
    if user_role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Only administrators can cancel event requests.")

    # --- Validate Event ID ---
    try:
        event_object_id = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=422, detail=f"Invalid ObjectId format for event_id: {event_id}")

    # --- Find the event ---
    # Fetch fields needed for cleanup
    event_to_cancel = await db.events.find_one(
        {"_id": event_object_id},
        {"approval_status": 1, "organization_id": 1, "schedule_id": 1, "request_document_key": 1}
    )
    if not event_to_cancel:
        raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found.")

    # Check if already cancelled to avoid redundant operations
    current_status = event_to_cancel.get("approval_status")
    if current_status == EventRequestStatus.CANCELLED.value:
         print(f"Event {event_id} is already cancelled.")
         return None # Return 204 as it's already in the desired state

    # --- Update Event Status to Cancelled ---
    try:
        update_result = await db.events.update_one(
            {"_id": event_object_id},
            {"$set": {"approval_status": EventRequestStatus.CANCELLED.value}}
        )
        if update_result.matched_count == 0:
            raise HTTPException(status_code=404, detail=f"Event request with ID '{event_id}' not found during cancellation update.")
        print(f"Event {event_id} status updated to Cancelled by admin.")
    except Exception as e:
        print(f"Error updating event {event_id} status to Cancelled: {e}")
        # Don't raise yet, proceed to cleanup if possible
        pass # Logged the error, cleanup might still work

    # --- Perform Full Cleanup (Including Schedule Deletion) ---
    await _perform_event_cleanup(event_object_id, event_to_cancel, db, delete_schedule=True)

    # --- Return No Content ---
    return None # FastAPI handles the 204 response

