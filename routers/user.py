from fastapi import APIRouter, Depends

from auth.auth_handler import get_current_active_user
from schemas import UserResponse
from modelsv1 import User

router = APIRouter()

@router.get("/users/me/", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    response_data = {
        "id": str(current_user["_id"]),
        "email": current_user["email"],
        "role": current_user["role"],
        "is_active": current_user["is_active"],
    }

    if current_user["role"] == "student":
        response_data["organization"] = current_user.get("organization")
    elif current_user["role"] == "admin":
        response_data["department"] = current_user.get("department")

    return response_data