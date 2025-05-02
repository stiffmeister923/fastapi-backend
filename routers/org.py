# routers/org.py

from fastapi import APIRouter, HTTPException, Depends, status, Path
from typing import List, Optional, Dict, Any # Added Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone

from database import get_database
# Import models and schemas
# Assuming schemas.py now includes 'department' in relevant Organization schemas
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
async def require_admin(current_user: dict = Depends(get_current_active_user)): # Assuming dict return
    """
    Dependency that raises an HTTPException if the current user is not an admin.
    """
    user_role = current_user.get("role")
    if not user_role or user_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin privileges required."
        )
    return current_user

# --- Helper Function to Prepare Org Response ---
# DEFINED HERE - Before any endpoint uses it
def _prepare_organization_response(org_doc: dict) -> dict:
    """Converts DB doc ObjectIds to strings for OrganizationResponse validation."""
    prepared_doc = org_doc.copy()
    if "_id" in prepared_doc and isinstance(prepared_doc["_id"], ObjectId):
        prepared_doc["_id"] = str(prepared_doc["_id"])
    else:
        raise ValueError("Organization document missing or has invalid _id")

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
        # Ensure events key exists if needed by OrganizationResponse schema
        if "events" in OrganizationResponse.model_fields:
             prepared_doc["events"] = []

    # Department should be copied automatically if present in org_doc
    # Ensure 'department' field exists in OrganizationResponse schema (schemas.py)
    if "department" not in prepared_doc and "department" in OrganizationResponse.model_fields:
        # Check if the field is actually optional in the Pydantic model
        # This requires inspecting the model's schema or fields.
        # A simpler approach is to ensure the DB query projection includes all necessary fields
        # or handle potential missing keys gracefully during Pydantic validation.
        # Setting to None might work if the field is Optional.
        prepared_doc["department"] = None # Set default if missing but expected

    # Ensure other fields required by the response schema exist
    for field_name, field_info in OrganizationResponse.model_fields.items():
        # Skip fields already handled ('id' alias, members, events, department if handled above)
        if field_name in prepared_doc or field_name == "id":
            continue
        # If field is required in schema but missing in doc, raise error or set default
        if not field_info.is_required():
            prepared_doc[field_name] = None # Set optional missing fields to None
        # else:
            # Handle required field missing - depends on data integrity guarantees
            # raise ValueError(f"Required field '{field_name}' missing in organization document {prepared_doc.get('_id')}")


    return prepared_doc


# --- API Endpoint to Create Organization ---
@router.post(
    "/create",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)]
)
async def create_organization(
    # Ensure OrganizationCreate schema in schemas.py includes 'department'
    organization_data: OrganizationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> OrganizationResponse:
    """
    Create a new organization, including its department. Requires admin privileges.
    """
    # Check for existing organization name
    existing_org = await db.organizations.find_one({"name": organization_data.name})
    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Organization with name '{organization_data.name}' already exists."
        )

    # Prepare the document dictionary for insertion
    organization_doc = organization_data.model_dump()
    organization_doc["members"] = []
    organization_doc["events"] = []
    organization_doc["created_at"] = datetime.now(timezone.utc)
    organization_doc["updated_at"] = None

    try:
        result = await db.organizations.insert_one(organization_doc)
        inserted_id = result.inserted_id
        created_organization_doc = await db.organizations.find_one({"_id": inserted_id})
        if not created_organization_doc:
             raise HTTPException(status_code=500, detail="Failed to retrieve created organization after insertion.")

        # Use helper to prepare response data
        response_data = _prepare_organization_response(created_organization_doc)
        return OrganizationResponse(**response_data)

    except ValueError as ve: # Catch error from helper
        print(f"Error preparing response data after creation: {ve}")
        raise HTTPException(status_code=500, detail="Error processing created organization data.")
    except Exception as e:
        print(f"Error during organization creation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create organization due to an internal error.")


# --- API Endpoint (List Organizations) ---
@router.get(
    "/list",
    response_model=List[OrganizationResponse]
    # dependencies=[Depends(get_current_active_user)] # Uncomment if auth needed
)
async def get_organization_list(
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> List[OrganizationResponse]:
    """
    Retrieve a list of all organizations.
    Requires authentication.
    """
    organizations_list = []
    organizations_cursor = db.organizations.find({})

    async for org_doc in organizations_cursor:
        try:
            # Use helper to prepare response data
            prepared_doc = _prepare_organization_response(org_doc)
            validated_org = OrganizationResponse(**prepared_doc)
            organizations_list.append(validated_org)
        except ValueError as ve: # Catch error from helper
             print(f"Warning: Skipping organization document due to preparation error: {ve} - Doc: {org_doc}")
             continue
        except Exception as e:
            print(f"Error validating prepared organization doc {org_doc.get('_id')}: {e}")
            # continue

    return organizations_list


# --- API Endpoint (Get Organization by ID) ---
@router.get(
    "/get/{org_id}",
    response_model=OrganizationResponse
    # dependencies=[Depends(get_current_active_user)] # Uncomment if auth needed
)
async def get_organization_by_id(
    org_id: str = Path(..., description="The MongoDB ObjectId of the organization"),
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

    organization_doc = await db.organizations.find_one({"_id": org_object_id})
    if organization_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found")

    try:
        # Use helper to prepare response data
        prepared_doc = _prepare_organization_response(organization_doc)
        return OrganizationResponse(**prepared_doc)
    except ValueError as ve: # Catch error from helper
        print(f"Error preparing response for organization {org_id}: {ve}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error processing organization data for response.")
    except Exception as e: # Catch Pydantic validation errors etc.
        print(f"Error validating response for organization {org_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error validating organization data for response.")


# --- API Endpoint (Update Organization by ID) ---
@router.put(
    "/update/{org_id}",
    response_model=OrganizationResponse,
    dependencies=[Depends(require_admin)] # Admin only
)
async def update_organization(
    update_data: OrganizationUpdate,
    org_id: str = Path(..., description="The MongoDB ObjectId of the organization to update"),
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> OrganizationResponse:
    """
    Update details (name, description, advisor, department) of an existing organization.
    Requires admin privileges. Only provide fields to be changed in the request body.
    """
    try:
        org_object_id = ObjectId(org_id)
    except InvalidId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID format: {org_id}")

    existing_org = await db.organizations.find_one({"_id": org_object_id})
    if not existing_org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found")

    update_doc = update_data.model_dump(exclude_unset=True)

    if "name" in update_doc and update_doc["name"] != existing_org.get("name"):
        name_conflict = await db.organizations.find_one(
            {"name": update_doc["name"], "_id": {"$ne": org_object_id}}
        )
        if name_conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Organization with name '{update_doc['name']}' already exists."
            )

    if update_doc:
        update_doc["updated_at"] = datetime.now(timezone.utc)
        try:
            update_result = await db.organizations.update_one(
                {"_id": org_object_id},
                {"$set": update_doc}
            )
            if update_result.matched_count == 0:
                raise HTTPException(status_code=404, detail=f"Organization with ID {org_id} disappeared during update.")
        except Exception as e:
            print(f"Error updating organization {org_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to update organization.")
    else:
        raise HTTPException(status_code=400, detail="No update data provided.")

    updated_org_doc = await db.organizations.find_one({"_id": org_object_id})
    if not updated_org_doc:
         raise HTTPException(status_code=500, detail="Failed to retrieve organization after update.")

    try:
        # *** FIX: Ensure helper function is called correctly ***
        prepared_doc = _prepare_organization_response(updated_org_doc)
        return OrganizationResponse(**prepared_doc)
    except ValueError as ve: # Catch error from helper
        print(f"Error preparing response for updated organization {org_id}: {ve}")
        raise HTTPException(status_code=500, detail="Error processing updated organization data.")
    except Exception as e: # Catch Pydantic validation errors etc.
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
    linked_user = await db.users.find_one({"organization_id": org_object_id})
    if linked_user: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot delete organization ID {org_id} as it has associated users (e.g., User email: {linked_user.get('email')}).")
    linked_event = await db.events.find_one({"organization_id": org_object_id})
    if linked_event: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot delete organization ID {org_id} as it has associated event requests (e.g., Event ID: {linked_event.get('_id')}).")
    linked_schedule = await db.schedules.find_one({"organization_id": org_object_id})
    if linked_schedule: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot delete organization ID {org_id} as it has associated schedules (e.g., Schedule ID: {linked_schedule.get('_id')}).")

    # Perform deletion
    try:
        delete_result = await db.organizations.delete_one({"_id": org_object_id})
        if delete_result.deleted_count == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found.")
        return None
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error deleting organization {org_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete organization.")
