from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import (
    sample_test,
    auth,
    user, 
    org, 
    events, 
    venue, 
    equipment,
    events,
    schedule,
    optimization )# Import equipment

import os
from dotenv import load_dotenv
app = FastAPI(debug=True)
# Load environment variables from .env file
app = FastAPI()

# Load environment FIRST before configuring middleware
load_dotenv()

# Configure CORS BEFORE adding routers
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",  # Add this alternative localhost
    os.getenv("LOCAL_FRONT", "http://localhost:5173"),
    os.getenv("DEPLOYED_FRONT")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]  # Add this line to expose headers
)

# Then add your routers
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(sample_test.router)
app.include_router(org.router)
app.include_router(venue.router)
app.include_router(equipment.router)
app.include_router(events.router)
app.include_router(schedule.router)
app.include_router(optimization.router)