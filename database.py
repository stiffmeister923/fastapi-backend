from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
# Load environment variables from .env file
load_dotenv()
# MongoDB connection string
MONGODB_URL = os.getenv("DATABASE_URL")
client = AsyncIOMotorClient(MONGODB_URL)
database = client.scheduler_db

# Dependency to get the database
async def get_database():
    return database