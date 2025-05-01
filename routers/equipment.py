# routers/equipment.py

from fastapi import APIRouter, HTTPException, Depends, status
from typing import List # Keep for potential future list endpoints
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime, timezone # Although not used yet, good practice

from database import get_database
# Import equipment-specific schemas
from schemas import EquipmentCreate, EquipmentResponse
# Import user schemas/enums needed for auth/RBAC
from schemas import UserResponse, UserRole 
# Import the database model if needed for internal logic (optional here)
# from modelsv1 import Equipment, EquipmentCreateInternal
# Import authentication dependencies
from auth.auth_handler import get_current_active_user

# Define the router for equipment-related endpoints
router = APIRouter(
    prefix="/equipment" # Tag for API documentation grouping
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

# === Endpoint to Create a New Equipment Item ===
@router.post(
    "/create", 
    response_model=EquipmentResponse, 
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)], # Apply admin check
    summary="Create a new equipment item (Admins only)"
)
async def create_equipment(
    equipment_data: EquipmentCreate, # Data from request body validated by EquipmentCreate schema
    db: AsyncIOMotorDatabase = Depends(get_database) # Database dependency
    # current_user: dict = Depends(require_admin) # Inject admin user if needed later
):
    """
    Allows an authenticated administrator to add a new equipment item to the system.

    - **name**: Name of the equipment (e.g., Projector, Whiteboard, Microphone).
    - **availability**: Initial availability status (e.g., "Available", "In Use", "Broken"). 
      Consider using an Enum for this later.
    """
    
    # 1. Optional: Check for duplicates (e.g., based on name)
    existing_equipment = await db.equipment.find_one({"name": equipment_data.name})
    if existing_equipment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Equipment with name '{equipment_data.name}' already exists."
        )

    # 2. Prepare data for database insertion
    # EquipmentCreate schema matches the required fields for the Equipment model (excluding ID)
    equipment_doc = equipment_data.model_dump()
    
    # Add any default fields not present in EquipmentCreate but needed in DB
    # (Based on your models, none seem needed here besides the auto-generated _id)
    # equipment_doc["added_at"] = datetime.now(timezone.utc) # Example if needed

    # 3. Insert into database (using "equipment" collection)
    try:
        insert_result = await db.equipment.insert_one(equipment_doc)
        inserted_id = insert_result.inserted_id

        # 4. Retrieve the newly created document to return in the response
        created_equipment_doc = await db.equipment.find_one({"_id": inserted_id})

        if not created_equipment_doc:
             raise HTTPException(status_code=500, detail="Failed to retrieve created equipment after insertion.")

        # 5. Prepare and Validate the Response
        # Convert ObjectId to string for the response model
        # EquipmentResponse uses alias="_id" for the 'id' field
        created_equipment_doc["_id"] = str(created_equipment_doc["_id"])
        
        # Pass the prepared dictionary to the response model for validation
        return EquipmentResponse(**created_equipment_doc)

    except Exception as e:
        print(f"Error creating equipment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create equipment due to an internal error.")

# === Endpoint to List All Equipment ===
@router.get(
    "/list",
    response_model=List[EquipmentResponse], # Return a list of EquipmentResponse objects
    
    summary="List all available equipment"
)
async def get_equipment_list(
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> List[EquipmentResponse]:
    """
    Retrieve a list of all equipment items currently in the system.
    Requires authentication.
    """
    equipment_list = []
    equipment_cursor = db.equipment.find({}) # Find all documents

    async for equipment_doc in equipment_cursor:
        try:
            # Convert ObjectId to string before validation
            equipment_doc["_id"] = str(equipment_doc["_id"])
            # Validate data against the response model
            equipment_list.append(EquipmentResponse(**equipment_doc))
        except Exception as e:
            # Log validation errors but continue processing others
            print(f"Error validating equipment data for ID {equipment_doc.get('_id')}: {e}")
            # Consider skipping this item or raising a 500 error if strictness is required
            # continue 
            
    return equipment_list

# === Endpoint to Get Specific Equipment by ID ===
@router.get(
    "/get/{equipment_id}", # Path parameter for the equipment ID
    response_model=EquipmentResponse,
 # Require authentication
    summary="Get details of specific equipment by ID"
)
async def get_equipment_by_id(
    # Use Path for validation and extraction of the equipment_id from the URL
    equipment_id: str ,
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> EquipmentResponse:
    """
    Retrieve the details of a specific equipment item by its unique MongoDB ObjectId.
    Requires authentication.
    """
    try:
        # Convert the validated string ID from the path parameter to ObjectId
        equipment_object_id = ObjectId(equipment_id)
    except InvalidId:
        # This case might be caught by Path regex, but good to have explicit check
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid equipment ID format: {equipment_id}")

    # Find the equipment in the database
    equipment_doc = await db.equipment.find_one({"_id": equipment_object_id})

    # If not found, raise 404 error
    if equipment_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Equipment with ID {equipment_id} not found")

    # Prepare the document for the response model
    try:
        equipment_doc["_id"] = str(equipment_doc["_id"])
        # Validate the prepared dictionary against the response model
        return EquipmentResponse(**equipment_doc)
    except Exception as e:
        # Catch potential errors during data preparation or Pydantic validation
        print(f"Error preparing response for equipment {equipment_id}: {e}")
        raise HTTPException(status_code=500, detail="Error processing equipment data for response.")
# PUT /equipment/{equipment_id} (to update an item - Admin only)
# DELETE /equipment/{equipment_id} (to delete an item - Admin only)

