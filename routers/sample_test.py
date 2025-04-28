from fastapi import APIRouter

router = APIRouter()

@router.get("/healthy")
async def root():
    return {"greeting": "Hello, World!", "message": "LESSSSSGAWWWW, fastapi backend  naten guys deployed to sa railway"}