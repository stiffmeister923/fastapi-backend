from fastapi import APIRouter, Depends, HTTPException # Added HTTPException
from bson import ObjectId # Import ObjectId if not already imported

from auth.auth_handler import get_current_active_user
from schemas import UserResponse, UserRole # Import UserRole if needed for comparison
# Assuming current_user from get_current_active_user is a dictionary-like object
# If it's a Pydantic model (modelsv1.User), adjust access accordingly (e.g., current_user.id)
# from modelsv1 import User # Uncomment if current_user is a User model instance

router = APIRouter()

@router.get("/users/me/", response_model=UserResponse)
async def read_users_me(current_user: dict = Depends(get_current_active_user)): # Assuming dict return type
    """
    Gets the details of the currently authenticated user.
    """
    # Basic check if current_user data is available
    if not current_user or not current_user.get("_id"):
         raise HTTPException(status_code=404, detail="Current user data not found.")

    # Prepare the response dictionary, converting IDs to strings
    response_data = {
        "id": str(current_user["_id"]), # Ensure _id exists and convert to string
        "email": current_user.get("email"), # Use .get() for safety
        "role": current_user.get("role"),
        "is_active": current_user.get("is_active", False), # Provide default if missing
    }

    user_role = current_user.get("role")
    if user_role == UserRole.STUDENT.value: # Compare with enum value
        org_id = current_user.get("organization")
        # --- FIX: Convert organization ObjectId to string ---
        if org_id and isinstance(org_id, ObjectId):
            response_data["organization"] = str(org_id)
        elif isinstance(org_id, str): # Handle case where it might already be a string
             response_data["organization"] = org_id
        else:
             response_data["organization"] = None # Ensure it's None if missing or invalid type
        # Make sure department is not included for students
        response_data["department"] = None
    elif user_role == UserRole.ADMIN.value: # Compare with enum value
        response_data["department"] = current_user.get("department")
        # Make sure organization is not included for admins
        response_data["organization"] = None
    else:
         # Handle unexpected roles if necessary
         response_data["organization"] = None
         response_data["department"] = None


    # Directly return the dictionary. FastAPI will validate it against UserResponse.
    return response_data

# Note: If get_current_active_user returns a Pydantic User model instance (`current_user: User`),
# you would use attribute access instead:
# async def read_users_me(current_user: User = Depends(get_current_active_user)):
#     response_data = {
#         "id": str(current_user.id), # Assuming 'id' is the field name in the User model
#         "email": current_user.email,
#         "role": current_user.role,
#         "is_active": current_user.is_active,
#         "organization": str(current_user.organization_id) if current_user.organization_id else None, # Convert ObjectId from model
#         "department": current_user.department,
#     }
#     # Filter based on role before returning
#     if current_user.role == UserRole.STUDENT:
#         response_data.pop("department", None)
#     elif current_user.role == UserRole.ADMIN:
#          response_data.pop("organization", None)
#     return response_data
