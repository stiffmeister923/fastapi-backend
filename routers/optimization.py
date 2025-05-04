# routers/optimization.py
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import date, datetime, timedelta, timezone # Added timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

# --- Project Imports ---
from database import get_database
from auth.auth_handler import require_admin
# --- MODIFIED: Import GA defaults ---
from genetic_algo_optimization import (
    optimize_weekly_schedule,
    DEFAULT_POPULATION_SIZE,
    DEFAULT_MAX_GENERATIONS,
    DEFAULT_MUTATION_RATE,
    DEFAULT_CROSSOVER_RATE
    # Import tournament size if you want to expose it too
    # DEFAULT_TOURNAMENT_SIZE
)
from schemas import UserResponse, ScheduleResponse, EventRequestStatus

# --- Pydantic Models for Requests/Responses ---

class FitnessWeights(BaseModel):
    """Defines the weights for the GA fitness function, adjustable by Admin."""
    venue_preference_match: float = Field(50.0, gt=0, description="Bonus for matching requested/preferred venue")
    date_match: float = Field(20.0, gt=0, description="Bonus for matching requested/preferred date")
    timeslot_match: float = Field(30.0, gt=0, description="Bonus for matching requested/preferred time slot")
    capacity_fit_penalty: float = Field(-10.0, lt=0, description="Penalty if estimated attendees exceed venue capacity")
    hectic_week_priority_bonus: float = Field(100.0, gt=0, description="Bonus for scheduling event during its designated Hectic Week")
    base_score_multiplier: float = Field(10.0, gt=0, description="Base score multiplier per successfully scheduled event")
    hard_constraint_penalty: float = Field(10000.0, gt=0, description="Penalty multiplier per hard constraint violation")

class OptimizeRequest(BaseModel):
    """Request body for triggering the weekly optimization."""
    start_date_str: str = Field(..., description="Start date of the week (Monday) in YYYY-MM-DD format")
    weights: FitnessWeights = Field(default_factory=FitnessWeights, description="Fitness function weights")

    # --- ADDED: Optional GA Parameters ---
    population_size: Optional[int] = Field(
        default=DEFAULT_POPULATION_SIZE, # Use imported default
        gt=10, # Example validation: must be > 10
        description="GA Population Size (e.g., 50-200)"
    )
    max_generations: Optional[int] = Field(
        default=DEFAULT_MAX_GENERATIONS, # Use imported default
        gt=0, # Example validation: must be positive
        description="GA Max Generations (e.g., 50-500)"
    )
    mutation_rate: Optional[float] = Field(
        default=DEFAULT_MUTATION_RATE, # Use imported default
        ge=0.0, le=1.0, # Must be between 0.0 and 1.0
        description="GA Mutation Rate (e.g., 0.05-0.25)"
    )
    crossover_rate: Optional[float] = Field(
        default=DEFAULT_CROSSOVER_RATE, # Use imported default
        ge=0.0, le=1.0, # Must be between 0.0 and 1.0
        description="GA Crossover Rate (e.g., 0.6-0.9)"
    )
    # Add tournament_size if needed, similar pattern
    # tournament_size: Optional[int] = Field(DEFAULT_TOURNAMENT_SIZE, gt=1, ...)
    # --- End Added Parameters ---

# --- Other Models (ProposedScheduleEntry, OptimizationProposal, AcceptProposalRequest) remain the same ---
class ProposedScheduleEntry(BaseModel):
    """Structure for a single entry in the proposed schedule."""
    event_id: str
    venue_id: str
    organization_id: str
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    is_optimized: bool = True
    class Config:
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class OptimizationProposal(BaseModel):
    """Response containing the proposed schedule and unscheduled events."""
    proposal_id: str = Field(..., description="A unique identifier for this proposal (e.g., timestamp or UUID)")
    target_week_start_date: date
    proposed_schedules: List[ProposedScheduleEntry]
    unscheduled_event_ids: List[str]
    optimization_report: Optional[Dict[str, Any]] = Field(None, description="Summary report of the optimization run")

class AcceptProposalRequest(BaseModel):
    """Request body for accepting a specific optimization proposal."""
    proposal_id: str = Field(..., description="The unique identifier of the proposal to accept")
    accepted_schedules: List[ProposedScheduleEntry]
    unscheduled_event_ids: List[str]


# --- Router Definition (Remains same) ---
router = APIRouter(
    prefix="/optimize",
    tags=["Optimization"],
    dependencies=[Depends(require_admin)]
)

# --- Temporary Storage (Remains same) ---
proposal_storage: Dict[str, OptimizationProposal] = {}

# --- API Endpoints ---

@router.post(
    "/week",
    response_model=OptimizationProposal,
    summary="Trigger weekly schedule optimization"
)
async def trigger_optimization(
    request: OptimizeRequest, # Request now includes optional GA params
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Triggers the Genetic Algorithm to optimize the schedule for the week
    starting on the provided date (assumed Monday).

    Allows optional override of Fitness Weights and GA Parameters.
    Requires Admin privileges.
    Returns the proposed schedule and report for Admin review.
    """
    try:
        start_date = date.fromisoformat(request.start_date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD.")

    end_date = start_date + timedelta(days=7)

    # --- Log received parameters ---
    print(f"Received optimization request for week starting {start_date}")
    print(f"Fitness Weights: {request.weights.model_dump()}")
    # Print GA params if they differ from defaults (or always print)
    print(f"GA Params: Pop={request.population_size}, Gens={request.max_generations}, Mut={request.mutation_rate}, Cross={request.crossover_rate}")


    try:
        # --- MODIFIED: Pass parameters to the GA function ---
        optimization_result = await optimize_weekly_schedule(
            start_date=start_date,
            end_date=end_date,
            db=db,
            weights=request.weights.model_dump(), # Pass weights as dict
            # Pass GA parameters from request
            population_size=request.population_size,
            max_generations=request.max_generations,
            mutation_rate=request.mutation_rate,
            crossover_rate=request.crossover_rate
            # Add tournament_size=request.tournament_size if implemented
        )
        # --- End Modified Call ---

        if optimization_result is None:
            # Consider returning a default report even on None result?
            # For now, stick to raising error.
            raise HTTPException(status_code=500, detail="Optimization process failed or returned no result.")

        # --- Unpack results including the report ---
        schedule_entries_for_db, unscheduled_ids_obj, optimization_report = optimization_result
        unscheduled_ids_str = [str(oid) for oid in unscheduled_ids_obj]

        # ... (rest of the response processing remains the same) ...
        proposed_schedules_response: List[ProposedScheduleEntry] = []
        for entry in schedule_entries_for_db:
             try:
                 proposed_schedules_response.append(
                     ProposedScheduleEntry(
                         event_id=str(entry["event_id"]),
                         venue_id=str(entry["venue_id"]),
                         organization_id=str(entry["organization_id"]),
                         scheduled_start_time=entry["scheduled_start_time"],
                         scheduled_end_time=entry["scheduled_end_time"],
                         is_optimized=entry["is_optimized"]
                     )
                 )
             except Exception as e:
                  print(f"Error converting schedule entry for response: {e} - Entry: {entry}")

        # Generate proposal ID
        proposal_id = f"proposal_{datetime.now(timezone.utc).isoformat()}"

        proposal = OptimizationProposal(
            proposal_id=proposal_id,
            target_week_start_date=start_date,
            proposed_schedules=proposed_schedules_response,
            unscheduled_event_ids=unscheduled_ids_str,
            optimization_report=optimization_report # Include the report
        )

        # --- Store proposal temporarily (DEMO ONLY) ---
        proposal_storage[proposal_id] = proposal
        print(f"Stored proposal {proposal_id}. Contains {len(proposal.proposed_schedules)} schedules.")

        return proposal

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Unexpected error during optimization trigger: {e}")
        # Log the full error traceback here in production
        raise HTTPException(status_code=500, detail=f"An internal error occurred during optimization: {str(e)}")

@router.post(
    "/accept_proposal",
    status_code=status.HTTP_200_OK,
    summary="Accept an optimization proposal and save the schedule"
)
async def accept_optimization_proposal(
    request: AcceptProposalRequest,
    db: AsyncIOMotorDatabase = Depends(get_database)
    # current_user: dict = Depends(require_admin) # Already enforced
):
    """
    Accepts a previously generated optimization proposal.
    - Saves the accepted schedule entries to the database.
    - Updates the status of corresponding events.

    Requires Admin privileges.
    """
    proposal_id = request.proposal_id

    # --- Retrieve proposal (DEMO ONLY - fetch from DB/cache in production) ---
    # In a real app, you wouldn't necessarily need storage if the frontend sends
    # the full data back in the AcceptProposalRequest, as done here.
    # stored_proposal = proposal_storage.get(proposal_id)
    # if not stored_proposal:
    #     raise HTTPException(status_code=404, detail=f"Proposal with ID '{proposal_id}' not found or expired.")
    # # Validate if request data matches stored data if needed
    # --------------------------------------------------------------------

    accepted_schedule_entries = request.accepted_schedules
    unscheduled_event_ids_str = request.unscheduled_event_ids

    print(f"Received request to accept proposal {proposal_id}.")
    print(f" - Accepted Schedules: {len(accepted_schedule_entries)}")
    print(f" - Unscheduled Event IDs: {len(unscheduled_event_ids_str)}")


    # --- Prepare data for Database Operations ---
    schedules_to_insert = []
    scheduled_event_ids_obj = []
    try:
        for entry_data in accepted_schedule_entries:
            # Convert string IDs back to ObjectId for DB insertion
            schedule_doc = entry_data.model_dump() # Get dict from Pydantic model
            schedule_doc["event_id"] = ObjectId(entry_data.event_id)
            schedule_doc["venue_id"] = ObjectId(entry_data.venue_id)
            schedule_doc["organization_id"] = ObjectId(entry_data.organization_id)
            # Ensure datetime objects are timezone-aware (UTC)
            schedule_doc["scheduled_start_time"] = entry_data.scheduled_start_time.astimezone(timezone.utc) if entry_data.scheduled_start_time.tzinfo else entry_data.scheduled_start_time.replace(tzinfo=timezone.utc)
            schedule_doc["scheduled_end_time"] = entry_data.scheduled_end_time.astimezone(timezone.utc) if entry_data.scheduled_end_time.tzinfo else entry_data.scheduled_end_time.replace(tzinfo=timezone.utc)

            schedules_to_insert.append(schedule_doc)
            scheduled_event_ids_obj.append(schedule_doc["event_id"])

        unscheduled_event_ids_obj = [ObjectId(id_str) for id_str in unscheduled_event_ids_str]

    except Exception as e:
        print(f"Error preparing data for DB operations: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid data format in accepted proposal: {e}")


    # --- Database Operations ---
    try:
        # 1. Insert new optimized schedule entries
        if schedules_to_insert:
            insert_result = await db.schedules.insert_many(schedules_to_insert, ordered=False)
            print(f"Inserted {len(insert_result.inserted_ids)} new schedule documents.")
        else:
            print("No schedule entries to insert.")

        # 2. Update status of successfully scheduled events to "Approved"
        if scheduled_event_ids_obj:
            update_scheduled_result = await db.events.update_many(
                {"_id": {"$in": scheduled_event_ids_obj}, "approval_status": EventRequestStatus.PENDING.value}, # Ensure we only update pending ones
                {"$set": {"approval_status": EventRequestStatus.APPROVED.value}} # Use correct enum value
            )
            print(f"Updated {update_scheduled_result.modified_count} scheduled events to '{EventRequestStatus.APPROVED.value}'.")

        # 3. Update status of unscheduled events to "Needs Alternatives"
        if unscheduled_event_ids_obj:
            update_unscheduled_result = await db.events.update_many(
                {"_id": {"$in": unscheduled_event_ids_obj}, "approval_status": EventRequestStatus.PENDING.value}, # Ensure we only update pending ones
                {"$set": {"approval_status": EventRequestStatus.NEEDS_ALTERNATIVES.value}} # Use correct enum value
            )
            print(f"Updated {update_unscheduled_result.modified_count} unscheduled events to '{EventRequestStatus.NEEDS_ALTERNATIVES.value}'.")

        # --- Cleanup temporary storage (DEMO ONLY) ---
        if proposal_id in proposal_storage:
            del proposal_storage[proposal_id]
        # ------------------------------------------

        return {"message": f"Proposal {proposal_id} accepted and processed successfully."}

    except Exception as e:
        print(f"Database error during proposal acceptance: {e}")
        # Consider rollback logic here if needed in a production scenario
        raise HTTPException(status_code=500, detail=f"Failed to process proposal acceptance due to a database error: {e}")


# --- (Optional) Reject Endpoint ---
@router.post(
    "/reject_proposal/{proposal_id}",
    status_code=status.HTTP_200_OK,
    summary="Reject (discard) an optimization proposal"
)
async def reject_optimization_proposal(
    proposal_id: str
    # db: AsyncIOMotorDatabase = Depends(get_database) # Not needed if just clearing memory
    # current_user: dict = Depends(require_admin) # Already enforced
):
    """
    Discards a previously generated optimization proposal.
    (In this demo implementation, just removes it from temporary storage).
    """
    # --- Cleanup temporary storage (DEMO ONLY) ---
    if proposal_id in proposal_storage:
        del proposal_storage[proposal_id]
        print(f"Discarded proposal {proposal_id}.")
        return {"message": f"Proposal {proposal_id} rejected and discarded."}
    else:
        # It's okay if it's already gone or never existed, maybe just log it.
        print(f"Proposal {proposal_id} not found in storage for rejection (might be already processed or invalid).")
        return {"message": f"Proposal {proposal_id} not found or already processed."}
        # Alternatively, raise 404:
        # raise HTTPException(status_code=404, detail=f"Proposal with ID '{proposal_id}' not found.")