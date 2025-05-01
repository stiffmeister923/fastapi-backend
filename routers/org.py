# routers/org.py

from fastapi import APIRouter, HTTPException, Depends, status
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

# --- NEW API Endpoint (List Organization Names and IDs) ---
@router.get(
        "/list",
        response_model=List[OrganizationResponse]
        # Add authentication dependency - any logged-in user can access this
 )
async def get_organization_list(
        db: AsyncIOMotorDatabase = Depends(get_database)
    ) -> List[OrganizationResponse]:
        """
        Retrieve a list of all organization IDs and names.
        Requires authentication.
        """
        organizations_cursor = db.organizations.find(
            {} # Projection: only include _id and name fields
        )

        organizations_list = []
        async for org_doc in organizations_cursor:
            # Convert _id to string before validating with Pydantic schema
            # The OrganizationNameId schema expects 'id' but maps from '_id' via alias
            org_doc["_id"] = str(org_doc["_id"])
            try:
                # Validate each document against the OrganizationNameId schema
                org_name_id = OrganizationResponse(**org_doc)
                organizations_list.append(org_name_id)
            except Exception as e:
                # Log error if a specific document fails validation
                print(f"Error validating organization doc {org_doc.get('_id')}: {e}")
                # Decide whether to skip this doc or raise an error
                # continue # Skip this document

        if not organizations_list and await db.organizations.count_documents({}) > 0:
             # This might indicate a validation issue with all documents
             print("Warning: No organizations passed validation, though documents exist.")
             # Consider raising an error or returning an empty list based on requirements

        return organizations_list

# --- NEW API Endpoint (Get Organization by ID) ---
@router.get(
    "/get/{org_id}", # Path parameter for the organization ID
    response_model=OrganizationResponse # Require authentication
)
async def get_organization_by_id(
    # Use Path for validation and extraction of the org_id from the URL
    org_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> OrganizationResponse:
    """
    Retrieve the details of a specific organization by its ID.
    Requires authentication.
    """
    try:
        # Convert the validated string ID from the path parameter to ObjectId
        org_object_id = ObjectId(org_id)
    except InvalidId:
        # This case might be caught by Path regex, but good to have explicit check
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID format: {org_id}")

    # Find the organization in the database
    organization_doc = await db.organizations.find_one({"_id": org_object_id})

    # If not found, raise 404 error
    if organization_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found")

    # --- Prepare the document for the response model ---
    # Convert ObjectId fields (_id, members) to strings
    try:
        if "_id" in organization_doc and isinstance(organization_doc["_id"], ObjectId):
            organization_doc["_id"] = str(organization_doc["_id"])
        else:
             # Should not happen if find_one returned a doc, but defensive check
             raise ValueError("Retrieved document missing _id") 

        if "members" in organization_doc:
            organization_doc["members"] = [
                str(member_id) for member_id in organization_doc["members"]
                if isinstance(member_id, ObjectId)
            ]
        else:
            organization_doc["members"] = [] # Ensure members key exists

        # Add checks for other required fields if necessary (e.g., created_at)
        if "created_at" not in organization_doc:
             print(f"Warning: 'created_at' missing from organization doc {org_id}. Setting to None for response.")
             organization_doc["created_at"] = None # Or handle as error depending on schema strictness

        # Validate the prepared dictionary against the response model
        return OrganizationResponse(**organization_doc)

    except Exception as e:
        # Catch potential errors during data preparation or Pydantic validation
        print(f"Error preparing response for organization {org_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error processing organization data for response.")
