# genetic_algo_optimization.py

import json
import random
import re
from datetime import datetime, timedelta, date, time, timezone
from typing import List, Dict, Any, Optional, Tuple, Set
from motor.motor_asyncio import AsyncIOMotorDatabase # Import motor type hint
from dateutil.parser import parse as dateutil_parse # Using dateutil for flexible parsing
from dateutil.relativedelta import relativedelta

# Assuming database functions and schemas might be needed later
# from database import get_database # If needed directly
# from schemas import ... # etc.
from bson import ObjectId # If ObjectIds are handled within this module

# --- Constants and Configuration ---
# These would ideally be loaded from a config file or environment variables
CONFIG_FILE_PATH = "academic_calendar_2024_2025.json" # Path to your config JSON
ACADEMIC_YEAR_STR = "2024-2025" 
# Default GA Parameters (Can be overridden by API request)
DEFAULT_POPULATION_SIZE = 50
DEFAULT_MAX_GENERATIONS = 50 # Start lower for testing
DEFAULT_MUTATION_RATE = 0.15 # Slightly higher mutation might be needed for scheduling
DEFAULT_CROSSOVER_RATE = 0.8
DEFAULT_TOURNAMENT_SIZE = 5

# Type Aliases for clarity
ScheduleSlot = Tuple[str, datetime, datetime] # venue_id_str, start_time_utc, end_time_utc
Chromosome = Dict[str, Optional[ScheduleSlot]] # event_id_str -> ScheduleSlot or None
DateTimeRange = Tuple[datetime, datetime] # start_utc, end_utc
FitnessResult = Tuple[float, int] # fitness_score, hard_violation_count

# --- Date Parsing Helper ---
# --- ADDED: Constraint Checking Helper for Post-Mortem ---
# --- Constraint Checking Helper for Post-Mortem ---
def _check_slot_constraints_for_reason(
    event: Dict[str, Any],
    venue_id: str,
    start_time: datetime,
    end_time: datetime,
    ga_data: Dict[str, Any]
) -> Optional[str]:
    """
    Checks a single potential slot against hard constraints (excluding internal conflicts)
    and returns the specific reason for failure, or None if valid.
    """
    venues_data = ga_data["venues"]
    constraints = ga_data["week_constraints"]
    # --- MODIFIED: Expect List[Dict] ---
    unavailable_general: List[Dict[str, Any]] = constraints["unavailable_general_slots"]
    venue_rules = constraints["venue_specific_rules"]
    is_hectic_week = venue_rules["is_hectic_week"] # Needed for logic below
    # --- MODIFIED: Expect Dict[str, List[Dict]] ---
    venue_blockages: Dict[str, List[Dict[str, Any]]] = venue_rules.get("blockages", {})
    target_start_date = ga_data["target_start_date"]
    target_end_date = ga_data["target_end_date"]
    equipment_requests = ga_data["equipment_requests_by_event"]
    equipment_id_to_name = ga_data["equipment_id_to_name"]
    equipment_counts = ga_data["equipment_counts"]
    event_id_str = str(event["_id"])

    # 1. Basic Time Bounds (Return specific reason strings)
    slot_date = start_time.date()
    if not (target_start_date <= slot_date < target_end_date):
        return f"Outside Target Week ({slot_date})"
    # --- MODIFIED: Use specific reason consistent with general constraints ---
    if start_time.weekday() == 6: # Sunday
        return "Sunday Blockage"
    try:
        start_time_naive = start_time.time().replace(tzinfo=None)
        if time(22, 0) <= start_time_naive or start_time_naive < time(6, 0):
             return f"Night Curfew" # Keep reason consistent

        next_day_6am = datetime.combine(start_time.date() + timedelta(days=1), time(6, 0), tzinfo=timezone.utc)
        if end_time > next_day_6am:
            return f"Night Curfew (Ends past 06:00)" # More specific

        end_time_naive = end_time.time().replace(tzinfo=None)
        if time(22, 0) < end_time_naive or (end_time_naive <= time(6, 0) and end_time.date() > start_time.date()):
            if end_time_naive != time(0,0):
                 return f"Night Curfew" # Keep reason consistent
    except Exception as e:
        return f"Time Bounds Check Error: {e}"

    # 2. General Unavailable Slots
    # --- MODIFIED: Use new structure and return reason ---
    for constraint_info in unavailable_general:
        try:
            unavail_start = constraint_info['start']
            unavail_end = constraint_info['end']
            if check_overlap(start_time, end_time, unavail_start, unavail_end):
                # Return the specific reason stored when constraint was processed
                return constraint_info.get('reason', "General Unavailability")
        except Exception as e:
            return f"General Unavailability Check Error: {e}"

    # 3. Venue-Specific Blockages (Only if NOT hectic)
    if not is_hectic_week:
        venue_doc = venues_data.get(venue_id)
        if not venue_doc:
            return f"Venue Not Found ({venue_id})"

        venue_type_key_base = None
        venue_type_lower = venue_doc.get("venue_type", "").lower()
        venue_name_lower = venue_doc.get("name", "").lower()
        if "classroom" in venue_type_lower: venue_type_key_base = "Classroom"
        elif "uls" in venue_name_lower: venue_type_key_base = "ULS"

        if venue_type_key_base:
            day_of_week = start_time.weekday()
            blockage_key = None
            if day_of_week < 5: blockage_key = f"{venue_type_key_base}_weekday"
            elif day_of_week == 5: blockage_key = f"{venue_type_key_base}_weekend_Sat" # Use specific Sat key

            if blockage_key and blockage_key in venue_blockages:
                for block in venue_blockages[blockage_key]:
                     rule_day = block.get("day")
                     if rule_day and start_time.strftime("%A") != rule_day:
                          continue
                     try:
                        block_start_t = block["start"]
                        block_end_t = block["end"]
                        event_start_t_naive = start_time.time().replace(tzinfo=None)
                        event_end_t_naive = end_time.time().replace(tzinfo=None)
                        if max(event_start_t_naive, block_start_t) < min(event_end_t_naive, block_end_t):
                            # --- MODIFIED: Return detailed reason ---
                            day_str = f" ({rule_day})" if rule_day else ""
                            # Use a clearer reason format
                            return f"Venue Blockage: {blockage_key}{day_str} ({block_start_t.strftime('%H:%M')}-{block_end_t.strftime('%H:%M')})"
                     except Exception as e:
                        return f"Venue Blockage Check Error: {e}"

    # 4. Equipment Conflicts (Check only for this event's request)
    requests_this_slot: Dict[str, int] = {}
    if event_id_str in equipment_requests:
        for req in equipment_requests[event_id_str]:
            equip_id_str = req["equipment_id_str"]
            quantity = req.get("quantity", 1)
            if equip_id_str in equipment_id_to_name:
                equip_name = equipment_id_to_name[equip_id_str]
                if equip_name not in equipment_counts: # Check if equipment exists in inventory
                     return f"Equipment Not Found: '{equip_name}'"
                requests_this_slot[equip_name] = requests_this_slot.get(equip_name, 0) + quantity
            else:
                 return f"Equipment Not Found (ID: {equip_id_str})" # Requested item doesn't exist

    for equip_name, requested_qty in requests_this_slot.items():
         available_qty = equipment_counts.get(equip_name, 0) # Should exist if checked above
         if requested_qty > available_qty:
             return f"Equipment Unavailable: '{equip_name}' (Requires {requested_qty}, Available {available_qty})"

    # 5. Capacity Check (Return detailed reason)
    # --- Re-added venue_doc check here for safety ---
    venue_doc = venues_data.get(venue_id)
    if venue_doc: # Should exist if we passed check #3
        capacity = venue_doc.get("occupancy")
        attendees = event.get("estimated_attendees")
        if capacity is not None and attendees is not None and attendees > capacity:
             return f"Capacity Exceeded (Needs {attendees}, Venue Capacity {capacity})"
    else:
         # Should have been caught earlier, but for safety
         return f"Venue Data Error ({venue_id})"

    # If all checks pass
    return None

    # --- ADDED: Post-Mortem Analysis Function ---
# --- Post-Mortem Analysis Function ---
def _run_post_mortem_analysis(
    unscheduled_event_ids: List[ObjectId],
    ga_data: Dict[str, Any]
) -> Dict[str, List[str]]:
    """
    Analyzes why events couldn't be scheduled by checking potential slots.
    Reports detailed conflict reasons.
    """
    print("\n--- Running Post-Mortem Analysis for Unscheduled Events ---")
    analysis_results: Dict[str, List[str]] = {}
    if not unscheduled_event_ids:
        return analysis_results

    pending_events_dict = {str(e["_id"]): e for e in ga_data["pending_events"]}
    venues = list(ga_data["venues"].values())
    if not venues:
        print("Warning [PostMortem]: No venues available to check.")
        for event_obj_id in unscheduled_event_ids:
             analysis_results[str(event_obj_id)] = ["Post-mortem skipped: No venues available."]
        return analysis_results

    target_start_date = ga_data["target_start_date"]
    target_end_date = ga_data["target_end_date"]
    start_datetime_utc = datetime.combine(target_start_date, time.min, tzinfo=timezone.utc)
    end_datetime_utc = datetime.combine(target_end_date, time.min, tzinfo=timezone.utc)

    # Define time slots to check per day - check more frequently
    times_to_check = [time(h, m) for h in range(6, 22) for m in (0, 30)] # Check every 30 mins 6am-9:30pm

    for event_obj_id in unscheduled_event_ids:
        event_id_str = str(event_obj_id)
        event = pending_events_dict.get(event_id_str)
        if not event: continue

        print(f"Analyzing Event: {event.get('event_name', event_id_str)}")
        event_analysis: List[str] = []
        # --- MODIFIED: Store full, unique reasons ---
        conflict_reasons: Set[str] = set()
        checked_any_slot = False # More accurate flag name

        # Calculate duration (remains same)
        duration = timedelta(hours=1.5) # Default
        if event.get("requested_time_start") and event.get("requested_time_end"):
             # Ensure they are timezone-aware before subtracting
             start_aware = event["requested_time_start"]
             end_aware = event["requested_time_end"]
             if start_aware.tzinfo is None: start_aware = start_aware.replace(tzinfo=timezone.utc)
             if end_aware.tzinfo is None: end_aware = end_aware.replace(tzinfo=timezone.utc)
             req_dur = end_aware - start_aware
             if req_dur > timedelta(0): duration = req_dur

        # Iterate through days
        current_date = target_start_date
        while current_date < target_end_date:
            # Iterate through venues
            for venue in venues:
                venue_id_str = str(venue["_id"])
                # Iterate through representative times
                for check_time in times_to_check:
                     checked_any_slot = True
                     start_attempt = datetime.combine(current_date, check_time, tzinfo=timezone.utc)
                     end_attempt = start_attempt + duration

                     # Ensure end time doesn't exceed the target end date (exclusive)
                     if end_attempt.date() >= target_end_date: continue

                     reason = _check_slot_constraints_for_reason(event, venue_id_str, start_attempt, end_attempt, ga_data)

                     if reason:
                         # --- MODIFIED: Add full reason ---
                         conflict_reasons.add(reason)
                         # Optimization: If a date is fully blocked (e.g. holiday),
                         # maybe stop checking other times/venues for that date? Optional.

            current_date += timedelta(days=1)

        # Populate report for this event
        if not checked_any_slot:
            event_analysis.append("Could not attempt any checks (check date range/venue data).")
        elif not conflict_reasons:
             event_analysis.append("No constraint conflicts found in sampled check slots. Failure likely due to conflicts between events (Internal Conflicts) not resolved by GA.")
        else:
             event_analysis.append("Potential blocking constraints identified:")
             # --- MODIFIED: List full, sorted reasons ---
             # Group reasons for better readability? (Optional)
             grouped_reasons = {}
             for r in conflict_reasons:
                 rtype = r.split(':')[0] if ':' in r else r
                 if rtype not in grouped_reasons: grouped_reasons[rtype] = set()
                 grouped_reasons[rtype].add(r)

             for reason_type in sorted(grouped_reasons.keys()):
                 for specific_reason in sorted(list(grouped_reasons[reason_type])):
                      event_analysis.append(f"- {specific_reason}")

        analysis_results[event_id_str] = event_analysis

    return analysis_results

def parse_date_string(date_str: str, year_start: int, year_end: int) -> List[date]:
    """
    Parses various date string formats into a list of specific date objects
    for the given academic year (e.g., 2024-2025).
    Handles formats like: "Aug 21", "Oct 31 - Nov 2", "Mar 15, 17-21".
    """
    parsed_dates = []
    date_str = date_str.strip()

    # Helper to determine the correct year based on month
    def get_year(month: int) -> int:
        # Assuming academic year starts mid-year (e.g., July/Aug)
        # Months from Jan to ~June/July belong to the end year
        # Months from ~July/Aug to Dec belong to the start year
        # Adjust the cutoff month as needed (e.g., 7 for July)
        cutoff_month = 7
        return year_end if month < cutoff_month else year_start

    try:
        # Format: "Month Day" (e.g., "Aug 21")
        if re.fullmatch(r"[A-Za-z]{3,}\s+\d{1,2}", date_str):
            dt = dateutil_parse(date_str).date()
            correct_year = get_year(dt.month)
            parsed_dates.append(dt.replace(year=correct_year))

        # Format: "Month Day - Day" (e.g., "Oct 14 - 19")
        elif match := re.fullmatch(r"([A-Za-z]{3,}\s+\d{1,2})\s+-\s+(\d{1,2})", date_str):
            start_str, end_day_str = match.groups()
            start_dt = dateutil_parse(start_str).date()
            correct_year = get_year(start_dt.month)
            start_dt = start_dt.replace(year=correct_year)
            end_day = int(end_day_str)
            if end_day < start_dt.day: # Should not happen in this format but safety check
                 print(f"Warning: End day before start day in '{date_str}'")
                 return []
            current_date = start_dt
            while current_date.day <= end_day:
                 # Check if month/year changed unexpectedly (shouldn't with this format)
                 if current_date.month != start_dt.month or current_date.year != start_dt.year: break
                 parsed_dates.append(current_date)
                 current_date += timedelta(days=1)

        # Format: "Month Day - Month Day" (e.g., "Oct 31 - Nov 2", "Dec 21 - Jan 9")
        elif match := re.fullmatch(r"([A-Za-z]{3,}\s+\d{1,2})\s+-\s+([A-Za-z]{3,}\s+\d{1,2})", date_str):
            start_str, end_str = match.groups()
            start_dt_naive = dateutil_parse(start_str).date()
            end_dt_naive = dateutil_parse(end_str).date()
            start_year = get_year(start_dt_naive.month)
            end_year = get_year(end_dt_naive.month) # Can be different if range crosses year boundary
            start_dt = start_dt_naive.replace(year=start_year)
            end_dt = end_dt_naive.replace(year=end_year)

            if start_dt > end_dt: # Handle year wrap around (e.g., Dec to Jan)
                 # Assume end_dt is in the next calendar year relative to start_dt
                 # This logic assumes ranges don't span more than ~6 months crossing the year boundary
                 if start_dt.month > 6 and end_dt.month < 7: # Likely Dec-Jan case
                      end_dt = end_dt.replace(year=start_year + 1) # Adjust end year if needed
                 else: # Or maybe Jan-Dec within same calendar year but crossing academic year boundary?
                      # This case needs careful thought based on how academic year is defined
                      print(f"Warning: Ambiguous year for range '{date_str}'. Assuming standard progression.")
                      # If start is e.g. Jan(year_end) and end is Feb(year_end)
                      if start_year == end_year and start_year == year_end:
                           pass # Standard case within end year
                      else: # Re-evaluate if start_dt > end_dt after year assignment
                           if start_dt > end_dt: # If still reversed, indicates potential issue
                                print(f"Error: Could not resolve date range order for '{date_str}'")
                                return []


            current_date = start_dt
            while current_date <= end_dt:
                parsed_dates.append(current_date)
                current_date += timedelta(days=1)

        # Format: "Month Day, Day - Day" or "Month Day, Day, Day" etc. (e.g., "Mar 15, 17 - 21", "Feb 24, 25 & Mar 1")
        # This requires more complex splitting logic (commas, '&', ranges)
        elif ',' in date_str or '&' in date_str:
             # Normalize separators (replace '&' with ',')
             normalized_str = date_str.replace('&', ',')
             parts = [p.strip() for p in normalized_str.split(',')]
             current_month_str = "" # Keep track of month context

             for part in parts:
                 if not part: continue
                 # Check if part specifies a month (e.g., "Mar 1")
                 month_match = re.match(r"([A-Za-z]{3,})\s+(\d{1,2})", part)
                 if month_match:
                      current_month_str = month_match.group(1)
                      day_str = month_match.group(2)
                      # Try parsing single date
                      try:
                           dt = dateutil_parse(f"{current_month_str} {day_str}").date()
                           correct_year = get_year(dt.month)
                           parsed_dates.append(dt.replace(year=correct_year))
                      except ValueError:
                           print(f"Warning: Could not parse part '{part}' in '{date_str}'")
                 # Check if part is a range (e.g., "17 - 21")
                 elif '-' in part and current_month_str:
                      range_match = re.match(r"(\d{1,2})\s*-\s*(\d{1,2})", part)
                      if range_match:
                           start_day = int(range_match.group(1))
                           end_day = int(range_match.group(2))
                           # Parse month with start day to get context
                           try:
                                context_dt = dateutil_parse(f"{current_month_str} {start_day}").date()
                                correct_year = get_year(context_dt.month)
                                current_date = context_dt.replace(year=correct_year)
                                while current_date.day <= end_day:
                                     if current_date.month != context_dt.month: break # Month changed
                                     parsed_dates.append(current_date)
                                     current_date += timedelta(days=1)
                           except ValueError:
                                print(f"Warning: Could not parse range part '{part}' in '{date_str}'")
                      else:
                           print(f"Warning: Unrecognized range format '{part}' in '{date_str}'")
                 # Check if part is just a day number (needs month context)
                 elif re.fullmatch(r"\d{1,2}", part) and current_month_str:
                      try:
                           dt = dateutil_parse(f"{current_month_str} {part}").date()
                           correct_year = get_year(dt.month)
                           parsed_dates.append(dt.replace(year=correct_year))
                      except ValueError:
                           print(f"Warning: Could not parse day part '{part}' in '{date_str}'")
                 else:
                      print(f"Warning: Unhandled part format '{part}' in '{date_str}'")

        # Handle "onwards" - interpret as single date for now
        elif "onwards" in date_str:
             date_part = date_str.replace("onwards", "").strip()
             if re.fullmatch(r"[A-Za-z]{3,}\s+\d{1,2}", date_part):
                  dt = dateutil_parse(date_part).date()
                  correct_year = get_year(dt.month)
                  parsed_dates.append(dt.replace(year=correct_year))
             else:
                  print(f"Warning: Could not parse 'onwards' date: {date_str}")

        else:
            print(f"Warning: Unrecognized date string format: {date_str}")

    except Exception as e:
        print(f"Error during date string parsing for '{date_str}': {e}")
        return [] # Return empty list on failure

    # Return unique sorted dates
    return sorted(list(set(parsed_dates)))


# --- Constraint Processing Function ---

# --- Constraint Processing Function ---
# --- Constraint Processing Function ---

def process_weekly_constraints(
    target_start_date: date,
    target_end_date: date, # Exclusive
    calendar_data: Dict[str, Any],
    academic_year_str: str = ACADEMIC_YEAR_STR
) -> Dict[str, Any]:
    """
    Processes calendar data and rules to create specific constraints for the target week.
    Returns reasons alongside general unavailable slots.
    """
    print(f"Processing constraints for week: {target_start_date} to {target_end_date}...")

    # 1. Initialization
    # --- MODIFIED: Store as List[Dict] to include reasons ---
    unavailable_general_slots: List[Dict[str, Any]] = []
    # --- MODIFIED: Store dicts for blockages too ---
    venue_blockages: Dict[str, List[Dict[str, Any]]] = {}
    try:
        year_start = int(academic_year_str.split('-')[0])
        year_end = int(academic_year_str.split('-')[1])
    except Exception as e:
        raise ValueError(f"Invalid academic_year_str format: {academic_year_str}. Error: {e}")

    # 2. Determine if Hectic Week (Logic remains the same)
    is_hectic_week = False
    for period in calendar_data.get('hectic_periods', []):
        parsed_hectic_dates = parse_date_string(period.get('date', ''), year_start, year_end)
        if not parsed_hectic_dates: continue
        min_hectic_date = min(parsed_hectic_dates)
        max_hectic_date = max(parsed_hectic_dates)
        # Check for overlap: week starts before period ends AND week ends after period starts
        if target_start_date <= max_hectic_date and target_end_date > min_hectic_date:
             is_hectic_week = True
             print(f"Target week overlaps with Hectic Period: {period.get('name')}")
             break # Found overlap, no need to check further

    # 3. Process General Unavailable Dates from Config
    blockage_categories = ['national_holidays', 'school_holidays_breaks', 'examination_periods']
    # Use a set to track dates already fully blocked by these primary categories
    all_blocked_dates: Set[date] = set()

    for category in blockage_categories:
        for entry in calendar_data.get('unavailable_dates', {}).get(category, []):
            # --- MODIFIED: Use specific event name as reason ---
            reason = entry.get("event", category.replace('_', ' ').title()) # Get specific event name or fallback
            parsed_dates = parse_date_string(entry.get('date', ''), year_start, year_end)
            for p_date in parsed_dates:
                # Process only if the date falls within the target optimization week
                if target_start_date <= p_date < target_end_date:
                    start_dt = datetime.combine(p_date, time.min, tzinfo=timezone.utc)
                    end_dt = datetime.combine(p_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
                    # --- MODIFIED: Append dict with specific reason ---
                    unavailable_general_slots.append({'start': start_dt, 'end': end_dt, 'reason': reason})
                    all_blocked_dates.add(p_date) # Mark this date as fully blocked

    # 4. Process "1 Week Before Exams" Constraint
    exam_period_starts = []
    for entry in calendar_data.get('unavailable_dates', {}).get('examination_periods', []):
         parsed_dates = parse_date_string(entry.get('date', ''), year_start, year_end)
         if parsed_dates:
              exam_period_starts.append(min(parsed_dates))

    for exam_start_date in sorted(list(set(exam_period_starts))):
        # Calculate the 7-day window before the exam start date
        pre_exam_start = exam_start_date - timedelta(days=7)
        current_pre_exam_date = pre_exam_start
        while current_pre_exam_date < exam_start_date:
             # Process only if the date falls within the target optimization week
             if target_start_date <= current_pre_exam_date < target_end_date:
                 # --- MODIFIED: Check if already blocked by ANY category above ---
                 if current_pre_exam_date not in all_blocked_dates:
                    start_dt = datetime.combine(current_pre_exam_date, time.min, tzinfo=timezone.utc)
                    end_dt = datetime.combine(current_pre_exam_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
                    # --- MODIFIED: Append dict with specific reason ---
                    unavailable_general_slots.append({'start': start_dt, 'end': end_dt, 'reason': "Pre-Exam Week Blockage"})
                    # We don't add to all_blocked_dates here, as pre-exam block might allow *some* activity
                    # (although current hard constraint treats it as full block)

             current_pre_exam_date += timedelta(days=1)

    # 5. Add Universal Time Constraints (Sunday, Night Curfew)
    current_iter_date = target_start_date
    while current_iter_date < target_end_date:
        # Sunday Blockage
        if current_iter_date.weekday() == 6: # 6 is Sunday
            # --- MODIFIED: Check if already blocked by category above ---
            if current_iter_date not in all_blocked_dates:
                 start_dt = datetime.combine(current_iter_date, time.min, tzinfo=timezone.utc)
                 end_dt = datetime.combine(current_iter_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
                 # --- MODIFIED: Append dict with specific reason ---
                 unavailable_general_slots.append({'start': start_dt, 'end': end_dt, 'reason': "Sunday Blockage"})
                 # Mark Sunday as blocked too, if needed elsewhere (optional)
                 # all_blocked_dates.add(current_iter_date)

        # Night Curfew (Applies regardless of other blocks)
        start_curfew = datetime.combine(current_iter_date, time(22, 0), tzinfo=timezone.utc)
        end_curfew = datetime.combine(current_iter_date + timedelta(days=1), time(6, 0), tzinfo=timezone.utc)
        # --- MODIFIED: Append dict with specific reason ---
        unavailable_general_slots.append({'start': start_curfew, 'end': end_curfew, 'reason': "Night Curfew"})

        current_iter_date += timedelta(days=1)

    # 6. Determine Venue Specific Rules based on Hectic Week status
    if not is_hectic_week:
        # --- ADDED: Debug print to check the source data ---
        standard_blocks_config = calendar_data.get('scheduling_constraints', {}).get('standard_venue_blockages', {})
        print(f"DEBUG: Loading standard venue blockages (is_hectic_week=False): {standard_blocks_config}")
        for venue_key, time_ranges in standard_blocks_config.items():
             parsed_ranges = []
             # Ensure time_ranges is a list
             if not isinstance(time_ranges, list):
                 print(f"Warning: Expected list for venue key '{venue_key}' in standard_venue_blockages, got {type(time_ranges)}. Skipping.")
                 continue
             for tr in time_ranges:
                 # Ensure tr is a dictionary with expected keys
                 if not isinstance(tr, dict) or 'start_time' not in tr or 'end_time' not in tr:
                     print(f"Warning: Invalid time range format {tr} for {venue_key}. Skipping.")
                     continue
                 try:
                      start_t = time.fromisoformat(tr['start_time'])
                      end_t = time.fromisoformat(tr['end_time'])
                      day_constraint = tr.get("day") # Optional day constraint
                      parsed_ranges.append({"start": start_t, "end": end_t, "day": day_constraint})
                 except (ValueError, KeyError, TypeError) as e:
                      print(f"Warning: Could not parse time range {tr} for {venue_key}: {e}")
             if parsed_ranges:
                  venue_blockages[venue_key] = parsed_ranges
        # --- ADDED: Debug print to see the result ---
        print(f"DEBUG: Parsed venue_blockages: {venue_blockages}")

    venue_specific_rules = {
        "is_hectic_week": is_hectic_week,
        "blockages": venue_blockages
    }

    # 7. Return Results
    # Sort by start time for more logical processing later if needed
    final_unavailable_slots = sorted(unavailable_general_slots, key=lambda x: x['start'])
    print(f"Processed constraints. Hectic Week: {is_hectic_week}. General unavailable slots: {len(final_unavailable_slots)}")

    return {
        "unavailable_general_slots": final_unavailable_slots, # Now a List[Dict]
        "venue_specific_rules": venue_specific_rules
    }
    
# --- Data Fetching Function ---

async def fetch_ga_data(start_date: date, end_date: date, db: AsyncIOMotorDatabase, week_constraints: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetches all necessary data for the GA run for the specified week.
    Requires pre-processed week_constraints.
    """
    start_datetime_utc = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_datetime_utc = datetime.combine(end_date, time.min, tzinfo=timezone.utc)

    print(f"Fetching GA data for week: {start_date} to {end_date}")

    # 1. Fetch Pending Events for the week
    # Ensure EventRequestStatus.PENDING.value matches the string in the DB
    pending_events_cursor = db.events.find({
        "approval_status": "Pending",
        "requested_date": { # Filter by originally requested date falling in the week
            "$gte": start_datetime_utc,
            "$lt": end_datetime_utc
        }
    })
    pending_events = await pending_events_cursor.to_list(length=None)
    # +++ START: Ensure requested times in pending events are UTC aware +++
    for event in pending_events:
        for field in ["requested_time_start", "requested_time_end"]:
            dt = event.get(field)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    event[field] = dt.replace(tzinfo=timezone.utc)
                    # Optional: log this conversion for debugging
                    # print(f"DEBUG [Fetch]: Converted naive {field} {dt} to UTC for pending event {event.get('_id')}")
                elif dt.tzinfo != timezone.utc:
                    event[field] = dt.astimezone(timezone.utc)
                    # Optional: log this conversion
                    # print(f"DEBUG [Fetch]: Converted non-UTC {field} {dt} to UTC for pending event {event.get('_id')}")
    # +++ END: Ensure requested times are UTC aware +++

    pending_event_ids = [event["_id"] for event in pending_events]
    print(f"Found {len(pending_events)} pending events.")

    # 2. Fetch Existing Non-Optimized Schedules for the week (for conflict checking)
    existing_schedules_cursor = db.schedules.find({
        "is_optimized": False,
        # Check for overlap with the target week
        "$and": [
             {"scheduled_start_time": {"$lt": end_datetime_utc}},
             {"scheduled_end_time": {"$gte": start_datetime_utc}}
         ]
    })
    existing_schedules = await existing_schedules_cursor.to_list(length=None)
        # Fetch raw schedules first
    raw_existing_schedules = await existing_schedules_cursor.to_list(length=None)

    # +++ START: Add Conversion Step +++
    existing_schedules = []
    for sched in raw_existing_schedules:
        try:
            start = sched.get("scheduled_start_time")
            end = sched.get("scheduled_end_time")

            if not isinstance(start, datetime) or not isinstance(end, datetime):
                print(f"Warning: Invalid time type in existing schedule {sched.get('_id')}. Skipping.")
                continue

            # Ensure they are aware UTC datetimes
            if start.tzinfo is None:
                sched["scheduled_start_time"] = start.replace(tzinfo=timezone.utc)
                print(f"DEBUG [Fetch]: Converted naive start {start} to UTC for existing schedule {sched.get('_id')}")
            elif start.tzinfo != timezone.utc:
                 sched["scheduled_start_time"] = start.astimezone(timezone.utc)
                 print(f"DEBUG [Fetch]: Converted non-UTC start {start} to UTC for existing schedule {sched.get('_id')}")

            if end.tzinfo is None:
                sched["scheduled_end_time"] = end.replace(tzinfo=timezone.utc)
                print(f"DEBUG [Fetch]: Converted naive end {end} to UTC for existing schedule {sched.get('_id')}")
            elif end.tzinfo != timezone.utc:
                sched["scheduled_end_time"] = end.astimezone(timezone.utc)
                print(f"DEBUG [Fetch]: Converted non-UTC end {end} to UTC for existing schedule {sched.get('_id')}")

            existing_schedules.append(sched) # Add the potentially modified schedule
        except Exception as e:
            # Log error and potentially skip this schedule if conversion fails
            print(f"Error processing existing schedule {sched.get('_id')}: {e}. Skipping.")
    # +++ END: Add Conversion Step +++
    print(f"Found {len(existing_schedules)} existing non-optimized schedules potentially conflicting.")

    # 3. Fetch All Venues
    venues_cursor = db.venues.find({})
    venues_list = await venues_cursor.to_list(length=None)
    venues_dict = {str(v["_id"]): v for v in venues_list} # Dict for faster lookup
    print(f"Found {len(venues_list)} venues.")

    # 4. Fetch All Equipment & Calculate Counts
    all_equipment_docs = await db.equipment.find({}).to_list(None)
    equipment_id_to_name: Dict[str, str] = {}
    equipment_counts: Dict[str, int] = {}
    for item in all_equipment_docs:
        item_id_str = str(item["_id"])
        name = item.get("name")
        if name:
            equipment_id_to_name[item_id_str] = name
            equipment_counts[name] = equipment_counts.get(name, 0) + 1
    print(f"Found {len(all_equipment_docs)} equipment items across {len(equipment_counts)} types.")

    # 5. Fetch Preferences for the pending events
    preferences_cursor = db.preferences.find({"event_id": {"$in": pending_event_ids}})
    preferences_list = await preferences_cursor.to_list(length=None)
    prefs_by_event: Dict[str, List[Dict[str, Any]]] = {}
    for pref in preferences_list:
        event_id_str = str(pref['event_id'])
        if event_id_str not in prefs_by_event:
            prefs_by_event[event_id_str] = []
        prefs_by_event[event_id_str].append(pref)
    print(f"Found preferences for {len(prefs_by_event)} pending events.")

    # 6. Fetch Equipment Requests for relevant events
    existing_schedule_event_ids = [s["event_id"] for s in existing_schedules]
    # Combine pending event IDs and IDs from existing schedules in the target week
    relevant_event_ids = list(set(pending_event_ids + existing_schedule_event_ids))
    requests_by_event_id: Dict[str, List[Dict[str, Any]]] = {}
    if relevant_event_ids: # Only query if there are events
         event_equipment_cursor = db.event_equipment.find({"event_id": {"$in": relevant_event_ids}})
         all_relevant_event_equipment = await event_equipment_cursor.to_list(None)
         for req in all_relevant_event_equipment:
              evt_id_str = str(req["event_id"])
              if evt_id_str not in requests_by_event_id: requests_by_event_id[evt_id_str] = []
              # Store equipment ID as string for consistency
              req["equipment_id_str"] = str(req["equipment_id"])
              requests_by_event_id[evt_id_str].append(req)
    print(f"Found equipment requests for {len(requests_by_event_id)} relevant events.")


    return {
        "pending_events": pending_events,
        "existing_schedules": existing_schedules,
        "venues": venues_dict,
        "equipment_counts": equipment_counts,
        "equipment_id_to_name": equipment_id_to_name,
        "equipment_requests_by_event": requests_by_event_id,
        "preferences": prefs_by_event,
        "week_constraints": week_constraints, # Pass processed constraints
        "target_start_date": start_date,
        "target_end_date": end_date
    }

# --- GA Core Components ---

def check_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    """Checks if two datetime ranges overlap."""
    return start1 < end2 and end1 > start2

def calculate_fitness(chromosome: Chromosome, ga_data: Dict[str, Any], weights: Dict[str, float]) -> FitnessResult:
    """
    Calculates the fitness score and hard constraint violations for a chromosome.
    Handles general constraints provided as List[Dict].
    """
    hard_constraint_violations = 0
    soft_constraint_score = 0.0
    num_scheduled = 0

    # Extract data for easier access
    venues_data = ga_data["venues"]
    existing_schedules = ga_data["existing_schedules"]
    constraints = ga_data["week_constraints"]
    # --- MODIFIED: Expect List[Dict] with 'start', 'end', 'reason' keys ---
    unavailable_general: List[Dict[str, Any]] = constraints["unavailable_general_slots"]
    venue_rules = constraints["venue_specific_rules"]
    # --- MODIFIED: Expect Dict[str, List[Dict]] with 'start', 'end', 'day' keys ---
    venue_blockages: Dict[str, List[Dict[str, Any]]] = venue_rules.get("blockages", {})
    is_hectic_week = venue_rules["is_hectic_week"]
    # --- End Modifications for Data Extraction ---
    pending_events_dict = {str(e["_id"]): e for e in ga_data["pending_events"]}
    prefs_by_event = ga_data["preferences"]
    equipment_requests = ga_data["equipment_requests_by_event"]
    equipment_id_to_name = ga_data["equipment_id_to_name"]
    equipment_counts = ga_data["equipment_counts"]
    target_start_date = ga_data["target_start_date"]
    target_end_date = ga_data["target_end_date"]

    # --- Pre-calculate concurrent slots (No changes needed here) ---
    active_slots_by_venue: Dict[str, List[Tuple[datetime, datetime, str]]] = {}
    for event_id_str, slot_data in chromosome.items():
        if slot_data:
            venue_id, start_time, end_time = slot_data
            if venue_id not in active_slots_by_venue: active_slots_by_venue[venue_id] = []
            active_slots_by_venue[venue_id].append((start_time, end_time, event_id_str))
    for existing in existing_schedules:
         venue_id = str(existing["venue_id"])
         # Ensure existing schedule times are timezone-aware (should be handled in fetch_ga_data)
         start_time = existing["scheduled_start_time"]
         end_time = existing["scheduled_end_time"]
         event_id = str(existing["event_id"])
         if venue_id not in active_slots_by_venue: active_slots_by_venue[venue_id] = []
         active_slots_by_venue[venue_id].append((start_time, end_time, f"existing_{event_id}"))

    # --- Evaluate each scheduled event ---
    processed_event_ids = set()
    for event_id_str, slot_data in chromosome.items():
        if not slot_data:
            continue # Event not scheduled

        num_scheduled += 1
        processed_event_ids.add(event_id_str)
        venue_id, start_time, end_time = slot_data
        original_event = pending_events_dict.get(event_id_str)
        if not original_event:
             hard_constraint_violations += 1
             print(f"Error: Event data not found for scheduled ID {event_id_str}")
             continue

        current_event_violations = 0

        # --- Hard Constraint Checks ---
        # 1. Basic Time Bounds (No changes needed here)
        try:
            slot_date = start_time.date()
            if not (target_start_date <= slot_date < target_end_date): current_event_violations += 1
            if start_time.weekday() == 6: current_event_violations += 1 # Sunday
            start_time_naive = start_time.time().replace(tzinfo=None)
            if time(22, 0) <= start_time_naive or start_time_naive < time(6, 0): current_event_violations += 1 # Night Curfew Start
            next_day_6am = datetime.combine(start_time.date() + timedelta(days=1), time(6, 0), tzinfo=timezone.utc)
            if end_time > next_day_6am: current_event_violations += 1 # Night Curfew End (Past 6am next day)
            end_time_naive = end_time.time().replace(tzinfo=None)
            if time(22, 0) < end_time_naive or (end_time_naive <= time(6, 0) and end_time.date() > start_time.date()):
                 if end_time_naive != time(0,0): current_event_violations += 1 # Night Curfew End (Before midnight but after 10pm)
        except TypeError as e:
             print(f"***** ERROR: TIME BOUNDS CHECK Type Error for Event {event_id_str} *****")
             print(f"***** Comparing {start_time} ({type(start_time)}) and {end_time} ({type(end_time)}) *****")
             raise e

        # 2. General Unavailable Slots (Holidays, Exams, Pre-Exam, Sunday, Curfew)
        # --- MODIFIED: Loop through list of dictionaries and use 'start'/'end' keys ---
        # print(f"DEBUG [GeneralUnavail]: Checking against {len(unavailable_general)} general slots.") # Optional Debug
        for constraint_info in unavailable_general:
            try:
                # Access start/end from the dictionary
                unavail_start = constraint_info['start']
                unavail_end = constraint_info['end']
                if check_overlap(start_time, end_time, unavail_start, unavail_end):
                    current_event_violations += 1
                    # Optional Debug: print(f"Violation: {event_id_str} overlaps {constraint_info.get('reason')} ({unavail_start} - {unavail_end})")
                    break # Found violation, no need to check more general constraints
            except (TypeError, KeyError) as e: # Catch potential errors accessing dict or comparing types
                 print(f"***** ERROR: GENERAL UNAVAIL CHECK Error for Event {event_id_str} *****")
                 print(f"***** Comparing slot {start_time}-{end_time} vs constraint {constraint_info} *****")
                 print(f"***** Error Details: {e} *****")
                 # Decide whether to raise or just count as violation
                 # current_event_violations += 1 # Count as violation if data is bad
                 raise e # Re-raise for now to catch data issues

        # 3. Venue-Specific Blockages (If not hectic)
        # --- MODIFIED: Check uses venue_blockages dict which should now contain dicts ---
        if not is_hectic_week:
            venue_doc = venues_data.get(venue_id)
            if venue_doc:
                venue_type_key_base = None
                venue_type_lower = venue_doc.get("venue_type", "").lower()
                venue_name_lower = venue_doc.get("name", "").lower()
                if "classroom" in venue_type_lower: venue_type_key_base = "Classroom"
                elif "uls" in venue_name_lower: venue_type_key_base = "ULS"

                if venue_type_key_base:
                    day_of_week = start_time.weekday()
                    blockage_key = None
                    if day_of_week < 5: blockage_key = f"{venue_type_key_base}_weekday"
                    elif day_of_week == 5: blockage_key = f"{venue_type_key_base}_weekend_Sat"

                    if blockage_key and blockage_key in venue_blockages:
                        # venue_blockages[blockage_key] should be a List[Dict]
                        for block in venue_blockages[blockage_key]:
                            rule_day = block.get("day") # Check optional day constraint
                            if rule_day and start_time.strftime("%A") != rule_day:
                                continue
                            try:
                                # Access start/end times from the block dictionary
                                block_start_t = block["start"] # Naive time
                                block_end_t = block["end"]     # Naive time
                                event_start_t_naive = start_time.time().replace(tzinfo=None)
                                event_end_t_naive = end_time.time().replace(tzinfo=None)
                                if max(event_start_t_naive, block_start_t) < min(event_end_t_naive, block_end_t):
                                    current_event_violations += 1
                                    # Optional debug: print(f"Violation: {event_id_str} overlaps venue blockage {blockage_key}")
                                    break # Violation found for this key
                            except (TypeError, KeyError) as e:
                                print(f"***** ERROR: VENUE BLOCK CHECK Error for Event {event_id_str} *****")
                                print(f"***** Block Key: {blockage_key}, Block Data: {block} *****")
                                print(f"***** Error Details: {e} *****")
                                raise e # Re-raise
                        # Exit outer loop if violation found for this key
                        if current_event_violations > 0 and blockage_key and blockage_key in venue_blockages:
                            # This check might be redundant if break inside inner loop works correctly
                            pass # Already incremented, will be caught outside loop

            else: # Venue ID invalid
                 current_event_violations += 1

        # 4. Conflicts with Other Slots (Internal Chromosome + Existing) - (No change needed here)
        # ... (keep internal conflict check logic) ...
        if venue_id in active_slots_by_venue:
            for other_start, other_end, other_event_id_ctx in active_slots_by_venue[venue_id]:
                 if other_event_id_ctx == event_id_str: continue # Skip self
                 try:
                      if check_overlap(start_time, end_time, other_start, other_end):
                           current_event_violations += 1
                           # Optional debug: print(f"Violation: {event_id_str} internal conflict with {other_event_id_ctx}")
                           break # Only need to count one conflict per slot
                 except TypeError as e:
                      print(f"***** ERROR: INTERNAL CONFLICT CHECK Type Error for Event {event_id_str} *****")
                      print(f"***** Comparing slot {start_time}-{end_time} vs other {other_start}-{other_end} ({other_event_id_ctx}) *****")
                      raise e

        # 5. Equipment Conflicts - (No change needed here, relies on fetched data)
        # ... (keep equipment check logic) ...
        concurrent_events_for_equip_check = [event_id_str]
        for other_venue_id, slots in active_slots_by_venue.items():
            for other_start, other_end, other_event_id_ctx in slots:
                if other_event_id_ctx == event_id_str: continue
                # Check chromosome events + existing schedules overlapping this time
                if check_overlap(start_time, end_time, other_start, other_end):
                    # Check if it's an event from the chromosome or an existing schedule
                    if other_event_id_ctx in pending_events_dict or other_event_id_ctx.startswith("existing_"):
                        id_to_check = other_event_id_ctx.replace("existing_", "") # Get original ID if existing
                        concurrent_events_for_equip_check.append(id_to_check)

        requests_this_slot: Dict[str, int] = {}
        for concurrent_event_id in set(concurrent_events_for_equip_check):
            if concurrent_event_id in equipment_requests:
                for req in equipment_requests[concurrent_event_id]:
                    equip_id_str = req["equipment_id_str"]
                    quantity = req.get("quantity", 1)
                    if equip_id_str in equipment_id_to_name:
                        equip_name = equipment_id_to_name[equip_id_str]
                        requests_this_slot[equip_name] = requests_this_slot.get(equip_name, 0) + quantity

        for equip_name, requested_qty in requests_this_slot.items():
            if equip_name not in equipment_counts:
                 # This equipment doesn't exist in the inventory at all
                 current_event_violations += 1
                 # Optional debug: print(f"Violation: Requested equipment '{equip_name}' not found in inventory.")
                 break
            available_qty = equipment_counts.get(equip_name, 0)
            if requested_qty > available_qty:
                current_event_violations += 1
                # Optional debug: print(f"Violation: Equipment '{equip_name}' overbooked ({requested_qty}/{available_qty})")
                break

        # --- Accumulate violations ---
        hard_constraint_violations += current_event_violations

        # --- Soft Constraint Scoring (Only if no hard violations for THIS slot) ---
        if current_event_violations == 0:
            # --- Soft constraint logic remains the same ---
            # It reads data but doesn't depend on the changed constraint *structures*
            current_event_score = weights.get('base_score_multiplier', 10.0)

            # Bonus for venue match
            venue_pref_score = 0
            requested_venue_id = str(original_event.get("requested_venue_id")) if original_event.get("requested_venue_id") else None
            if venue_id == requested_venue_id:
                venue_pref_score = weights.get("venue_preference_match", 50.0)
            else:
                 event_prefs = prefs_by_event.get(event_id_str, [])
                 for pref in event_prefs:
                      preferred_venue_id = str(pref.get("preferred_venue_id")) if pref.get("preferred_venue_id") else None
                      if venue_id == preferred_venue_id:
                           venue_pref_score = weights.get("venue_preference_match", 50.0) * 0.8
                           break
            current_event_score += venue_pref_score

            # Bonus for date/time match
            datetime_match_score = 0
            # ... (existing date/time match logic - ensure checks are robust) ...
            requested_date = original_event.get("requested_date").date() if original_event.get("requested_date") else None
            requested_start_time = original_event.get("requested_time_start") # Aware UTC
            requested_end_time = original_event.get("requested_time_end")     # Aware UTC
            if requested_date and requested_date == start_time.date():
                 datetime_match_score += weights.get("date_match", 20.0) * 0.5
                 if requested_start_time and requested_end_time:
                      try:
                          if check_overlap(start_time, end_time, requested_start_time, requested_end_time):
                              datetime_match_score += weights.get("timeslot_match", 30.0) * 0.5
                      except TypeError as e: # Catch potential errors comparing aware/naive if fetch failed
                          print(f"***** ERROR: SOFT DATE/TIME (Request) CHECK Type Error for Event {event_id_str} *****")
                          # Don't raise, just skip bonus
            # Preference Check
            if datetime_match_score < (weights.get("date_match", 20.0) + weights.get("timeslot_match", 30.0)):
                event_prefs = prefs_by_event.get(event_id_str, [])
                for pref in event_prefs:
                      pref_date = pref.get("preferred_date") # This might be just date or datetime
                      if isinstance(pref_date, datetime): pref_date = pref_date.date() # Ensure it's a date obj
                      pref_start = pref.get("preferred_time_slot_start") # Aware UTC?
                      pref_end = pref.get("preferred_time_slot_end")     # Aware UTC?
                      current_pref_score = 0
                      if pref_date and pref_date == start_time.date():
                           current_pref_score += weights.get("date_match", 20.0) * 0.5
                           if pref_start and pref_end:
                                try: # Ensure pref times are aware UTC before check
                                    if pref_start.tzinfo is None: pref_start = pref_start.replace(tzinfo=timezone.utc)
                                    if pref_end.tzinfo is None: pref_end = pref_end.replace(tzinfo=timezone.utc)
                                    if check_overlap(start_time, end_time, pref_start, pref_end):
                                        current_pref_score += weights.get("timeslot_match", 30.0) * 0.5
                                except (TypeError, AttributeError) as e: # Catch errors
                                     print(f"***** ERROR: SOFT DATE/TIME (Preference) CHECK Type Error for Event {event_id_str} *****")
                                     # Skip bonus for this pref
                      datetime_match_score = max(datetime_match_score, current_pref_score * 0.8)
            current_event_score += datetime_match_score

            # Bonus for Hectic Week prioritization
            # ... (existing hectic week logic) ...
            requested_orig_date = original_event.get("requested_date").date() if original_event.get("requested_date") else None
            if requested_orig_date and is_hectic_week:
                # Check if the original requested date falls within any defined hectic period
                # This requires calendar_data, pass it within ga_data?
                # Assuming calendar_data is accessible via ga_data for simplicity here
                calendar_data_local = ga_data.get("_calendar_data_ref", {}) # Need to ensure calendar_data is passed in ga_data
                year_start_local = ga_data.get("_year_start", 2024) # Pass these too
                year_end_local = ga_data.get("_year_end", 2025)
                for period in calendar_data_local.get('hectic_periods', []):
                     parsed_hectic_dates = parse_date_string(period.get('date', ''), year_start_local, year_end_local)
                     if parsed_hectic_dates and min(parsed_hectic_dates) <= requested_orig_date <= max(parsed_hectic_dates):
                          current_event_score += weights.get("hectic_week_priority_bonus", 100.0)
                          break

            # Penalty for capacity violation
            # ... (existing capacity logic) ...
            if venue_doc:
                capacity = venue_doc.get("occupancy")
                attendees = original_event.get("estimated_attendees")
                if capacity is not None and attendees is not None and attendees > capacity:
                    over_capacity = attendees - capacity
                    penalty = weights.get("capacity_fit_penalty", -10.0) * (1 + over_capacity / max(1, capacity)) # Avoid div by zero
                    current_event_score += penalty

            soft_constraint_score += current_event_score

    # --- Final Fitness Calculation ---
    final_fitness = soft_constraint_score - (hard_constraint_violations * weights.get('hard_constraint_penalty', 10000.0))
    return (final_fitness, int(hard_constraint_violations))


def initialize_population(size: int, ga_data: Dict[str, Any]) -> List[Chromosome]:
    """Creates the initial population of potential weekly schedules."""
    population = []
    pending_events = ga_data["pending_events"]
    venues = list(ga_data["venues"].values())
    start_date = ga_data["target_start_date"]
    # end_date = ga_data["target_end_date"]

    print(f"Initializing population of size {size}...")
    if not venues:
        print("Error: No venues available for initialization. Cannot create population.")
        return []
    if not pending_events:
        print("No pending events to schedule.")
        return [{} for _ in range(size)] # Return empty chromosomes

    for _ in range(size):
        chromosome: Chromosome = {}
        for event in pending_events:
            event_id_str = str(event["_id"])
            # Attempt to schedule with higher probability
            if random.random() < 0.9: # 90% chance to try scheduling
                chosen_venue = random.choice(venues)
                venue_id_str = str(chosen_venue["_id"])

                # Try using requested date/time as a starting point sometimes
                use_requested = random.random() < 0.5 and event.get("requested_date") and event.get("requested_time_start")
                schedule_date = None
                start_time = None

                if use_requested:
                    req_date = event["requested_date"].date()
                    # Check if requested date is within the target week and not Sunday
                    if ga_data["target_start_date"] <= req_date < ga_data["target_end_date"] and req_date.weekday() != 6:
                         schedule_date = req_date
                         start_time = event["requested_time_start"]
                         # Basic check: ensure start time isn't in curfew
                         if time(22,0) <= start_time.time() or start_time.time() < time(6,0):
                              start_time = None # Invalidate if requested start is bad

                # If not using requested or requested was invalid, assign random
                if not schedule_date or not start_time:
                     attempts = 0
                     while attempts < 10: # Limit attempts to find a non-Sunday date
                          day_offset = random.randint(0, 6)
                          schedule_date = start_date + timedelta(days=day_offset)
                          if schedule_date.weekday() != 6: break # Found non-Sunday
                          attempts += 1
                     if schedule_date.weekday() == 6: # Failed to find non-Sunday
                          chromosome[event_id_str] = None; continue

                     # Assign random start hour (between 6 AM and ~21 PM)
                     start_hour = random.randint(6, 20) # Avoid starting right at 21:xx to give buffer
                     start_minute = random.choice([0, 15, 30, 45]) # More granular
                     start_time = datetime.combine(schedule_date, time(start_hour, start_minute), tzinfo=timezone.utc)


                # Calculate duration from request or default
                duration = timedelta(hours=1.5) # Default duration
                if event.get("requested_time_start") and event.get("requested_time_end"):
                     req_dur = event["requested_time_end"] - event["requested_time_start"]
                     if req_dur > timedelta(0): duration = req_dur

                end_time = start_time + duration

                # Basic check: end time validity
                # Compare naive time components
                if end_time.date() > start_time.date() and end_time.time().replace(tzinfo=None) > time(6,0):
                    chromosome[event_id_str] = None # Spans past 6am next day
                    continue
                # Compare naive time components
                if end_time.time().replace(tzinfo=None) > time(22,0) and end_time.time().replace(tzinfo=None) != time(0,0): # Allow ending at 22:00
                    chromosome[event_id_str] = None # Ends after 22:00
                    continue


                chromosome[event_id_str] = (venue_id_str, start_time, end_time)

            else:
                chromosome[event_id_str] = None # Event not scheduled

        population.append(chromosome)
    print("Population initialized.")
    return population


def selection(population: List[Chromosome], fitness_results: List[FitnessResult], k: int) -> Chromosome:
    """Selects a parent using tournament selection based on fitness score."""
    # Ensure population and fitness_results are aligned and not empty
    if not population or not fitness_results or len(population) != len(fitness_results):
         print("Error: Population or fitness results invalid for selection.")
         # Return a random one or raise error? Returning random for now.
         return random.choice(population) if population else {}


    # Ensure k is not larger than population size
    actual_k = min(k, len(population))
    if actual_k <= 0: # Handle edge case where population might be 0 or k is invalid
        return population[0] if population else {}


    tournament_indices = random.sample(range(len(population)), actual_k)
    tournament_contenders = [(population[i], fitness_results[i][0]) for i in tournament_indices] # Use fitness score (index 0)

    # Find the best contender based on fitness score
    winner = max(tournament_contenders, key=lambda x: x[1])
    return winner[0] # Return the chromosome


def crossover(parent1: Chromosome, parent2: Chromosome, ga_data: Dict[str, Any], rate: float) -> Tuple[Chromosome, Chromosome]:
    """Performs uniform crossover between two parent chromosomes."""
    if random.random() >= rate:
        return parent1.copy(), parent2.copy() # No crossover

    child1, child2 = {}, {}
    event_ids = list(ga_data["pending_events"]) # Use full list of potential events

    for event in event_ids:
        event_id_str = str(event["_id"])
        slot1 = parent1.get(event_id_str)
        slot2 = parent2.get(event_id_str)

        if random.random() < 0.5:
            child1[event_id_str] = slot1
            child2[event_id_str] = slot2
        else:
            child1[event_id_str] = slot2
            child2[event_id_str] = slot1

    return child1, child2


def mutate(chromosome: Chromosome, ga_data: Dict[str, Any], rate: float) -> Chromosome:
    """Applies mutation to a chromosome."""
    mutated_chromosome = chromosome.copy()
    event_ids = [str(e["_id"]) for e in ga_data["pending_events"]]
    venues = list(ga_data["venues"].values())
    start_date = ga_data["target_start_date"]

    if not venues: return mutated_chromosome # Cannot mutate venue if none exist

    for event_id_str in event_ids:
        if random.random() < rate:
            # Mutation strategy: Try to reschedule the event randomly
            # Could also try: slightly shift time, change venue only, swap two events

            # --- Generate a new random valid slot ---
            new_slot = None
            attempts = 0
            while attempts < 5: # Try a few times to find a better random slot
                chosen_venue = random.choice(venues)
                venue_id_str = str(chosen_venue["_id"])
                day_offset = random.randint(0, 6)
                schedule_date = start_date + timedelta(days=day_offset)

                if schedule_date.weekday() == 6: # Skip Sunday
                    attempts += 1; continue

                # Get original duration or default
                original_event = next((e for e in ga_data["pending_events"] if str(e["_id"]) == event_id_str), None)
                duration = timedelta(hours=1.5) # Default
                if original_event and original_event.get("requested_time_start") and original_event.get("requested_time_end"):
                    req_dur = original_event["requested_time_end"] - original_event["requested_time_start"]
                    if req_dur > timedelta(0): duration = req_dur

                start_hour = random.randint(6, 20)
                start_minute = random.choice([0, 15, 30, 45])
                start_time = datetime.combine(schedule_date, time(start_hour, start_minute), tzinfo=timezone.utc)
                end_time = start_time + duration


                if end_time.date() > start_time.date() and end_time.time().replace(tzinfo=None) > time(6,0): continue # ADD .replace(tzinfo=None)
                if end_time.time().replace(tzinfo=None) > time(22,0) and end_time.time().replace(tzinfo=None) != time(0,0): continue # ADD .replace(tzinfo=None) TWICE
                start_time_naive = start_time.time().replace(tzinfo=None) # Make naive explicitly
                if time(22,0) <= start_time_naive or start_time_naive < time(6,0): continue # Compare naive with naive
                new_slot = (venue_id_str, start_time, end_time)
                break # Found a potentially valid slot
            # --- End random slot generation ---

            mutated_chromosome[event_id_str] = new_slot # Assign new slot (could be None if attempts failed)

    return mutated_chromosome


# --- Main GA Function ---
# --- Main GA Function ---
async def optimize_weekly_schedule(
    start_date: date,
    end_date: date, # Exclusive
    db: AsyncIOMotorDatabase,
    weights: Dict[str, float],
    population_size: int = DEFAULT_POPULATION_SIZE,
    max_generations: int = DEFAULT_MAX_GENERATIONS,
    mutation_rate: float = DEFAULT_MUTATION_RATE,
    crossover_rate: float = DEFAULT_CROSSOVER_RATE,
    tournament_size: int = DEFAULT_TOURNAMENT_SIZE
# ) -> Optional[Tuple[List[Dict[str, Any]], List[ObjectId]]]: # OLD RETURN TYPE
) -> Optional[Tuple[List[Dict[str, Any]], List[ObjectId], Dict[str, Any]]]: # NEW RETURN TYPE (includes report)
    """
    Runs the genetic algorithm to optimize the schedule for the given week.
    """
    print(f"\n=== Starting GA Optimization ===")
    print(f"Target Week: {start_date} to {end_date}")
    print(f"Parameters: Pop={population_size}, Gens={max_generations}, Mut={mutation_rate}, Cross={crossover_rate}")
    print(f"Fitness Weights: {weights}")

    start_datetime_utc = datetime.combine(start_date, time.min, tzinfo=timezone.utc) # Needed for filtering constraints report
    end_datetime_utc = datetime.combine(end_date, time.min, tzinfo=timezone.utc) # Needed for filtering constraints report

    # --- ADDED: Initialize report data ---
    report_data: Dict[str, Any] = {
        "input_event_count": 0,
        "ga_params": {"pop": population_size, "gens": max_generations, "mut": mutation_rate, "cross": crossover_rate},
        "final_fitness": None,
        "final_violations": None,
        "is_hectic_week": False,
        "active_general_constraints": [],
        "active_venue_blockages": {},
        "unscheduled_event_analysis": {},
        "summary": "Optimization did not complete fully."
    }

    # 1. Load Config Data (Keep as is)
    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            calendar_data = json.load(f)
    except Exception as e:
         print(f"Error loading config: {e}")
         report_data["summary"] = f"Error loading config: {e}"
         return ([], [], report_data) # Return report even on early failure

    # 2. Process Constraints & Fetch Week-Specific Data (Keep as is)
    try:
        week_constraints = process_weekly_constraints(start_date, end_date, calendar_data)
        ga_data = await fetch_ga_data(start_date, end_date, db, week_constraints)

        # --- ADDED: Populate report with initial data ---
        report_data["input_event_count"] = len(ga_data.get("pending_events", []))
        report_data["is_hectic_week"] = ga_data["week_constraints"]["venue_specific_rules"]["is_hectic_week"]

        # Format active general constraints for the report including reason
        active_reasons_set = set() # Keep track of unique reasons encountered
        formatted_constraints = []
        # Ensure ga_data contains the updated structure (list of dicts)
        general_constraints_list = ga_data["week_constraints"].get("unavailable_general_slots", [])
        # Filter for constraints relevant to the target week
        relevant_constraints = [
            c for c in general_constraints_list
            # Use simplified overlap check: constraint starts before week ends AND constraint ends after week starts
            if c['start'] < end_datetime_utc and c['end'] > start_datetime_utc
        ]
        # Sort by start time for readability
        relevant_constraints.sort(key=lambda x: x['start'])
        for c in relevant_constraints:
            reason = c.get('reason', 'Unknown General Constraint')
            # Format: "Reason: Start(YYYY-MM-DD HH:MM) - End(YYYY-MM-DD HH:MM)"
            formatted_constraints.append(
                f"{reason}: {c['start'].strftime('%Y-%m-%d %H:%M')} - {c['end'].strftime('%Y-%m-%d %H:%M')}"
            )
            active_reasons_set.add(reason) # Track unique reasons

        report_data["active_general_constraints"] = formatted_constraints
        # report_data["active_general_constraint_types"] = sorted(list(active_reasons_set)) # Optionally store unique types
        # --- End Update ---

        # --- Venue Blockage Formatting (Check JSON if this remains empty) ---
        if not report_data["is_hectic_week"]:
            formatted_blockages = {}
            venue_block_dict = ga_data["week_constraints"]["venue_specific_rules"].get("blockages", {})
            if not venue_block_dict:
                 print("Warning: Parsed venue_blockages is empty for non-hectic week. Check JSON.")
            for key, blocks in venue_block_dict.items():
                formatted_blockages[key] = [f"{b['start'].strftime('%H:%M')}-{b['end'].strftime('%H:%M')}" + (f" ({b['day']})" if b.get('day') else "") for b in blocks]
            report_data["active_venue_blockages"] = formatted_blockages
        # --- End Venue Blockage ---


        if not ga_data.get("pending_events"):
            print("No pending events for this week. Optimization finished.")
            report_data["summary"] = "No pending events to schedule."
            # Ensure final state is captured even if exiting early
            report_data["final_fitness"] = 0.0 # No events, fitness is 0
            report_data["final_violations"] = 0 # No events, violations is 0
            return ([], [], report_data)

    except Exception as e:
        print(f"Error fetching data or processing constraints for GA: {e}")
        report_data["summary"] = f"Error fetching data/processing constraints: {e}"
        report_data["input_event_count"] = len(ga_data.get("pending_events", [])) if "ga_data" in locals() else 0

        return ([], [], report_data)

    all_input_event_ids_obj = [e["_id"] for e in ga_data["pending_events"]]

    # 3. Initialize Population (Keep as is)
    population = initialize_population(population_size, ga_data)
    if not population and ga_data.get("pending_events"):
         print("Error: Failed to initialize population.")
         report_data["summary"] = "Failed to initialize GA population."
         return ([], ga_data.get("pending_events", []), report_data) # Return all events as unscheduled

    # 4. GA Loop (Keep as is)
    print(f"\n--- Running GA Generations ---")
    best_fitness_overall = -float('inf')
    best_chromosome_overall = None
    best_violation_count = float('inf')
    # ... (rest of the GA loop remains the same) ...
    # --- GA Loop Finished ---

    print(f"--- GA Finished. Final Best -> Fitness={best_fitness_overall:.2f}, Violations={best_violation_count} ---")
    # --- ADDED: Update report with final GA state ---
    report_data["final_fitness"] = None if best_fitness_overall == -float('inf') else best_fitness_overall

    report_data["final_violations"] = None if best_violation_count == float('inf') else best_violation_count


    # 5. Process Best Solution & Verify Hard Constraints
    if not best_chromosome_overall:
        print("No suitable schedule found by GA.")
        # ... (rest of this block) ...
        # --- Update report summary here ---
        report_data["summary"] = "GA could not find any potentially valid schedule configuration."
        # --- ADDED: Run post-mortem if no chromosome found ---
        analysis = _run_post_mortem_analysis(all_input_event_ids_obj, ga_data)
        report_data["unscheduled_event_analysis"] = analysis
        return ([], all_input_event_ids_obj, report_data)

    # --- Verification Step ---
    # Recalculate fitness/violations for the final best chromosome for certainty
    final_fitness, final_violations = calculate_fitness(best_chromosome_overall, ga_data, weights)
    print(f"Verifying best chromosome. Recalculated Fitness={final_fitness:.2f}, Violations={final_violations}")
    # --- Update report again with verified values ---
    # --- MODIFIED: Update report again with verified values, handling potential infinity ---
    report_data["final_fitness"] = None if final_fitness == -float('inf') else final_fitness
    report_data["final_violations"] = None if final_violations == float('inf') else final_violations

    final_schedule_entries = []
    unscheduled_event_ids_obj = []
    scheduled_ids_in_best_obj = set()

    if final_violations > 0:
        print(f"Warning: The best solution found still contains {final_violations} hard constraint violations. No schedule will be proposed.")
        # Treat all events as unscheduled
        unscheduled_event_ids_obj = all_input_event_ids_obj
        # --- ADDED: Run post-mortem if best solution is invalid ---
        analysis = _run_post_mortem_analysis(unscheduled_event_ids_obj, ga_data)
        report_data["unscheduled_event_analysis"] = analysis
        report_data["summary"] = f"Best solution found violated {final_violations} hard constraints. Treating all events as unscheduled."
        return ([], unscheduled_event_ids_obj, report_data)
    else:
        # Convert verified chromosome to DB format
        print("Best chromosome passed verification. Generating proposal...")
        # ... (loop to create final_schedule_entries and scheduled_ids_in_best_obj remains the same) ...
        for event_id_str, slot in best_chromosome_overall.items():
            if slot:
                venue_id, start_time, end_time = slot
                original_event = next((e for e in ga_data["pending_events"] if str(e["_id"]) == event_id_str), None)
                if original_event:
                    event_obj_id = original_event["_id"]
                    schedule_entry = {
                        "event_id": event_obj_id,
                        "venue_id": ObjectId(venue_id),
                        "organization_id": original_event["organization_id"],
                        "scheduled_start_time": start_time,
                        "scheduled_end_time": end_time,
                        "is_optimized": True,
                    }
                    final_schedule_entries.append(schedule_entry)
                    scheduled_ids_in_best_obj.add(event_obj_id)

        # Identify unscheduled events
        unscheduled_event_ids_obj = list(set(all_input_event_ids_obj) - scheduled_ids_in_best_obj)

        if unscheduled_event_ids_obj:
             # --- ADDED: Run post-mortem for events GA couldn't schedule ---
             analysis = _run_post_mortem_analysis(unscheduled_event_ids_obj, ga_data)
             report_data["unscheduled_event_analysis"] = analysis
             report_data["summary"] = f"GA found a valid schedule for {len(final_schedule_entries)} events, but could not schedule {len(unscheduled_event_ids_obj)}. See analysis for details."
        else:
             report_data["summary"] = f"GA successfully found a valid schedule for all {len(final_schedule_entries)} events."


    print(f"Processing complete. Proposed: {len(final_schedule_entries)} schedules. Unscheduled: {len(unscheduled_event_ids_obj)} events.")
    # --- Return schedule, unscheduled list, AND the report ---
    return (final_schedule_entries, unscheduled_event_ids_obj, report_data)