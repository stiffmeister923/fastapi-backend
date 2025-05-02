# routers/org.py

from fastapi import APIRouter, HTTPException, Depends, status, Path
from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime, timezone # Import timezone

from database import get_database
# Import models and schemas
# No longer strictly need Organization model here for insertion prep
# from modelsv1 import Organization
from schemas import (
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
    UserResponse,
    UserRole
)
from auth.auth_handler import get_current_active_user

router = APIRouter(prefix="/org", tags=["Organizations"])

# --- Role-Based Access Control Dependency ---
async def require_admin(current_user: UserResponse = Depends(get_current_active_user)):
    # Assuming get_current_active_user returns a dict-like object
    user_role = current_user.get("role")
    if not user_role or user_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin privileges required."
        )
    return current_user

# --- API Endpoint ---
@router.post(
    "/create",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)]
)
async def create_organization(
    organization_data: OrganizationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> OrganizationResponse:
    """
    Create a new organization. Requires admin privileges.
    """
    # Check for existing organization
    existing_org = await db.organizations.find_one({"name": organization_data.name})
    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Organization with name '{organization_data.name}' already exists."
        )

    # --- Prepare the document dictionary for insertion ---
    # Start with data from the request body schema
    organization_doc = organization_data.model_dump()

    # Explicitly add fields needed for the database document that aren't in OrganizationCreate
    organization_doc["members"] = [] # Initialize members as an empty list
    organization_doc["events"] = [] # Initialize members as an empty list
    organization_doc["created_at"] = datetime.now(timezone.utc) # Explicitly add current UTC time
    organization_doc["updated_at"] = None # Initialize updated_at as None

    # Note: We are letting MongoDB generate the _id automatically upon insertion.

    try:
        # Insert the new organization document
        result = await db.organizations.insert_one(organization_doc)
        inserted_id = result.inserted_id

        # Retrieve the newly created organization document using the inserted ID
        created_organization_doc = await db.organizations.find_one({"_id": inserted_id})

        if not created_organization_doc:
             # This case should be rare if insert_one succeeded, but good to check
             raise HTTPException(status_code=500, detail="Failed to retrieve created organization after insertion.")

        # --- Manual Conversion BEFORE Pydantic Validation for Response ---
        # The retrieved document now includes the MongoDB-generated _id and the created_at we added.
        # Convert ObjectId fields to strings needed for the OrganizationResponse model.

        # Ensure '_id' exists and convert it
        if "_id" in created_organization_doc and isinstance(created_organization_doc["_id"], ObjectId):
             # Prepare the dict for the response model, which expects 'id' (or '_id' via alias) as string
             created_organization_doc["_id"] = str(created_organization_doc["_id"])
        else:
             # Handle unexpected case where _id might be missing or not ObjectId
             print(f"Warning: '_id' field missing or not an ObjectId in retrieved document: {created_organization_doc}")
             raise HTTPException(status_code=500, detail="Error processing created organization ID.")


        # Ensure 'members' exists and convert any potential ObjectIds (though it should be empty here)
        if "members" in created_organization_doc:
             created_organization_doc["members"] = [
                 str(member_id) for member_id in created_organization_doc["members"]
                 if isinstance(member_id, ObjectId)
             ]
        else:
             created_organization_doc["members"] = [] # Ensure members key exists

        # 'created_at' should exist because we added it before insertion.
        # 'updated_at' should also exist.
        # Pydantic will validate the datetime format for 'created_at'.

        # Pass the prepared dictionary (with string IDs and datetime objects) to the response model
        return OrganizationResponse(**created_organization_doc)

    except Exception as e:
        print(f"Error during organization creation or response preparation: {e}") # Basic logging
        # Consider more specific error handling based on exception type
        raise HTTPException(status_code=500, detail=f"Failed to create organization due to an internal error: {e}")

@router.get(
    "/list",
    response_model=List[OrganizationResponse]
    # Add authentication dependency if needed
    # dependencies=[Depends(get_current_active_user)]
)
async def get_organization_list(
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> List[OrganizationResponse]:
    """
    Retrieve a list of all organizations.
    Requires authentication.
    """
    organizations_list = []
    # Fetch all fields needed by OrganizationResponse
    organizations_cursor = db.organizations.find({})

    async for org_doc in organizations_cursor:
        # --- Prepare the document for validation ---
        prepared_doc = org_doc.copy() # Work on a copy

        # Convert primary _id
        if "_id" in prepared_doc and isinstance(prepared_doc["_id"], ObjectId):
            prepared_doc["_id"] = str(prepared_doc["_id"])
        else:
            print(f"Warning: Skipping organization document due to missing or invalid _id: {prepared_doc}")
            continue # Skip this document if _id is bad

        # *** FIX: Convert ObjectIds in the 'members' list to strings ***
        if "members" in prepared_doc:
            prepared_doc["members"] = [
                str(member_id) for member_id in prepared_doc.get("members", []) # Use .get for safety
                if isinstance(member_id, ObjectId)
            ]
        else:
             prepared_doc["members"] = [] # Ensure members key exists

        # *** FIX: Convert ObjectIds in the 'events' list to strings (if applicable) ***
        # Assuming 'events' is also a list of ObjectIds in your DB model
        if "events" in prepared_doc:
             prepared_doc["events"] = [
                 str(event_id) for event_id in prepared_doc.get("events", []) # Use .get for safety
                 if isinstance(event_id, ObjectId)
             ]
        else:
             # Ensure events key exists if needed by OrganizationResponse schema
             # Check your OrganizationResponse schema definition
             if "events" in OrganizationResponse.model_fields:
                 prepared_doc["events"] = []


        # Add checks/defaults for other required fields if necessary (e.g., created_at)
        if "created_at" not in prepared_doc:
             print(f"Warning: 'created_at' missing from organization doc {prepared_doc.get('_id')}. Setting to None.")
             # Assign a default or handle based on schema requirements. Pydantic might raise error if required.
             # prepared_doc["created_at"] = None # Example: Set to None if optional

        # --- Validate the prepared document ---
        try:
            # Validate against the OrganizationResponse schema
            validated_org = OrganizationResponse(**prepared_doc)
            organizations_list.append(validated_org)
        except Exception as e:
            # Log error if a specific document fails validation AFTER preparation
            print(f"Error validating prepared organization doc {prepared_doc.get('_id')}: {e}")
            # Decide whether to skip this doc or raise an error
            # continue # Skip this document

    # Commenting out the warning for now as the fix should resolve it
    # if not organizations_list and await db.organizations.count_documents({}) > 0:
    #      print("Warning: No organizations passed validation, though documents exist.")

    return organizations_list

# --- NEW API Endpoint (Get Organization by ID) ---
# --- API Endpoint (Get Organization by ID) ---
@router.get(
    "/get/{org_id}",
    response_model=OrganizationResponse
    # Add authentication dependency if needed
    # dependencies=[Depends(get_current_active_user)]
)
async def get_organization_by_id(
    org_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> OrganizationResponse:
    """
    Retrieve the details of a specific organization by its ID.
    Requires authentication.
    """
    try:
        org_object_id = ObjectId(org_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID format: {org_id}")

    # Find the organization in the database
    organization_doc = await db.organizations.find_one({"_id": org_object_id})

    if organization_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found")

    # --- Prepare the document for the response model ---
    try:
        prepared_doc = organization_doc.copy() # Work on a copy

        if "_id" in prepared_doc and isinstance(prepared_doc["_id"], ObjectId):
            prepared_doc["_id"] = str(prepared_doc["_id"])
        else:
             raise ValueError("Retrieved document missing _id")

        if "members" in prepared_doc:
            prepared_doc["members"] = [
                str(member_id) for member_id in prepared_doc.get("members", [])
                if isinstance(member_id, ObjectId)
            ]
        else:
            prepared_doc["members"] = []

        if "events" in prepared_doc:
             prepared_doc["events"] = [
                 str(event_id) for event_id in prepared_doc.get("events", [])
                 if isinstance(event_id, ObjectId)
             ]
        else:
             if "events" in OrganizationResponse.model_fields:
                 prepared_doc["events"] = []

        # Add checks for other required fields if necessary (e.g., created_at)
        if "created_at" not in prepared_doc:
             print(f"Warning: 'created_at' missing from organization doc {org_id}. Setting to None for response.")
             # prepared_doc["created_at"] = None # Or handle as error depending on schema strictness

        # Validate the prepared dictionary against the response model
        return OrganizationResponse(**prepared_doc)

    except Exception as e:
        print(f"Error preparing response for organization {org_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error processing organization data for response.")



# --- API Endpoint (Update Organization by ID) ---
@router.put(
    "/update/{org_id}",
    response_model=OrganizationResponse,
    dependencies=[Depends(require_admin)] # Admin only
)
async def update_organization(
    # Body parameter first
    update_data: OrganizationUpdate,
    # Path parameter
    org_id: str = Path(..., description="The MongoDB ObjectId of the organization to update"),
    # Dependency
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> OrganizationResponse:
    """
    Update details of an existing organization. Requires admin privileges.
    Only provide fields to be changed in the request body.
    """
    try:
        org_object_id = ObjectId(org_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID format: {org_id}")

    # Check if organization exists
    existing_org = await db.organizations.find_one({"_id": org_object_id})
    if not existing_org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found")

    # Prepare update document, excluding fields not provided in the request
    update_doc = update_data.model_dump(exclude_unset=True)

    # Prevent updating 'members' or 'events' via this endpoint (if needed)
    # update_doc.pop("members", None)
    # update_doc.pop("events", None)

    # Check for name conflict if name is being changed
    if "name" in update_doc and update_doc["name"] != existing_org.get("name"):
        name_conflict = await db.organizations.find_one(
            {"name": update_doc["name"], "_id": {"$ne": org_object_id}}
        )
        if name_conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, # Or 409 Conflict
                detail=f"Organization with name '{update_doc['name']}' already exists."
            )

    # Add timestamp for update
    if update_doc:
        update_doc["updated_at"] = datetime.now(timezone.utc)

        try:
            update_result = await db.organizations.update_one(
                {"_id": org_object_id},
                {"$set": update_doc}
            )
            if update_result.matched_count == 0:
                # Should be caught above, but safety check
                raise HTTPException(status_code=404, detail=f"Organization with ID {org_id} disappeared during update.")

        except Exception as e:
            print(f"Error updating organization {org_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to update organization.")
    else:
        # No fields provided for update
        raise HTTPException(status_code=400, detail="No update data provided.")

    # Retrieve the updated document
    updated_org_doc = await db.organizations.find_one({"_id": org_object_id})
    if not updated_org_doc:
         raise HTTPException(status_code=500, detail="Failed to retrieve organization after update.")

    # Prepare and return the response
    try:
        prepared_doc = _prepare_organization_response(updated_org_doc)
        return OrganizationResponse(**prepared_doc)
    except ValueError as ve:
        print(f"Error preparing response for updated organization {org_id}: {ve}")
        raise HTTPException(status_code=500, detail="Error processing updated organization data.")
    except Exception as e:
        print(f"Error validating response for updated organization {org_id}: {e}")
        raise HTTPException(status_code=500, detail="Error validating updated organization data.")


# --- API Endpoint (Delete Organization by ID) ---
@router.delete(
    "/delete/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)] # Admin only
)
async def delete_organization(
    org_id: str = Path(..., description="The MongoDB ObjectId of the organization to delete"),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Delete an existing organization. Requires admin privileges.

    **Important:** This operation will fail if the organization has any
    associated users, events, or schedules.
    """
    try:
        org_object_id = ObjectId(org_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID format: {org_id}")

    # --- Conflict Checks ---
    # 1. Check for associated users
    linked_user = await db.users.find_one({"organization_id": org_object_id})
    if linked_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete organization ID {org_id} as it has associated users (e.g., User email: {linked_user.get('email')})."
        )

    # 2. Check for associated events
    linked_event = await db.events.find_one({"organization_id": org_object_id})
    if linked_event:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete organization ID {org_id} as it has associated event requests (e.g., Event ID: {linked_event.get('_id')})."
        )

    # 3. Check for associated schedules (if organization_id is stored in schedules)
    # Make sure your Schedule model/schema includes organization_id
    linked_schedule = await db.schedules.find_one({"organization_id": org_object_id})
    if linked_schedule:
         raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete organization ID {org_id} as it has associated schedules (e.g., Schedule ID: {linked_schedule.get('_id')})."
         )
    # --- End Conflict Checks ---

    # Perform deletion
    try:
        delete_result = await db.organizations.delete_one({"_id": org_object_id})

        if delete_result.deleted_count == 0:
            # If no conflicts were found but deletion failed, the org likely didn't exist
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found.")

        #  204 No Content on success
        return None

    except HTTPException as http_exc:
        raise http_exc # Re-raise 404 or 409
    except Exception as e:
        print(f"Error deleting organization {org_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete organization.")
