# genetic_algo_optimization.py

import json
import random
import re
from datetime import datetime, timedelta, date, time, timezone
from dateutil import tz # Make sure this import is present
from typing import List, Dict, Any, Optional, Tuple, Set
from motor.motor_asyncio import AsyncIOMotorDatabase
from dateutil.parser import parse as dateutil_parse
from dateutil.relativedelta import relativedelta
from bson import ObjectId

# Define PHT timezone globally
PHT_TZ = tz.gettz('Asia/Manila')

# --- Constants and Configuration ---
CONFIG_FILE_PATH = "academic_calendar_2024_2025.json"
ACADEMIC_YEAR_STR = "2024-2025"
DEFAULT_POPULATION_SIZE = 50
DEFAULT_MAX_GENERATIONS = 50
DEFAULT_MUTATION_RATE = 0.15
DEFAULT_CROSSOVER_RATE = 0.8
DEFAULT_TOURNAMENT_SIZE = 5

# Type Aliases
ScheduleSlot = Tuple[str, datetime, datetime] # venue_id_str, start_time_utc, end_time_utc
Chromosome = Dict[str, Optional[ScheduleSlot]] # event_id_str -> ScheduleSlot or None
FitnessResult = Tuple[float, int] # fitness_score, hard_violation_count

# --- Date Parsing Helper ---
def parse_date_string(date_str: str, year_start: int, year_end: int) -> List[date]:
    parsed_dates = []
    date_str = date_str.strip()
    cutoff_month = 7 # July

    def get_year(month: int) -> int:
        return year_end if month < cutoff_month else year_start

    try:
        if re.fullmatch(r"[A-Za-z]{3,}\s+\d{1,2}", date_str):
            dt = dateutil_parse(date_str).date()
            correct_year = get_year(dt.month)
            parsed_dates.append(dt.replace(year=correct_year))
        elif match := re.fullmatch(r"([A-Za-z]{3,}\s+\d{1,2})\s+-\s+(\d{1,2})", date_str):
            start_str, end_day_str = match.groups()
            start_dt = dateutil_parse(start_str).date()
            correct_year = get_year(start_dt.month)
            start_dt = start_dt.replace(year=correct_year)
            end_day = int(end_day_str)
            current_date = start_dt
            while current_date.day <= end_day and current_date.month == start_dt.month and current_date.year == start_dt.year:
                parsed_dates.append(current_date)
                current_date += timedelta(days=1)
        elif match := re.fullmatch(r"([A-Za-z]{3,}\s+\d{1,2})\s+-\s+([A-Za-z]{3,}\s+\d{1,2})", date_str):
            start_str, end_str = match.groups()
            start_dt_naive = dateutil_parse(start_str).date()
            end_dt_naive = dateutil_parse(end_str).date()
            start_year = get_year(start_dt_naive.month)
            end_year = get_year(end_dt_naive.month)
            start_dt = start_dt_naive.replace(year=start_year)
            end_dt = end_dt_naive.replace(year=end_year)
            if start_dt > end_dt and start_dt.month > 6 and end_dt.month < 7: # Dec - Jan crossing
                end_dt = end_dt.replace(year=start_year + 1)
            current_date = start_dt
            while current_date <= end_dt:
                parsed_dates.append(current_date)
                current_date += timedelta(days=1)
        elif ',' in date_str or '&' in date_str:
            normalized_str = date_str.replace('&', ',')
            parts = [p.strip() for p in normalized_str.split(',')]
            current_month_str = ""
            for part in parts:
                if not part: continue
                month_match = re.match(r"([A-Za-z]{3,})\s+(\d{1,2})", part)
                if month_match:
                    current_month_str = month_match.group(1)
                    day_str = month_match.group(2)
                    try:
                        dt = dateutil_parse(f"{current_month_str} {day_str}").date()
                        correct_year = get_year(dt.month)
                        parsed_dates.append(dt.replace(year=correct_year))
                    except ValueError: print(f"Warning: Could not parse part '{part}' in '{date_str}' with month context '{current_month_str}'")
                elif '-' in part and current_month_str:
                    range_match = re.match(r"(\d{1,2})\s*-\s*(\d{1,2})", part)
                    if range_match:
                        start_day, end_day = int(range_match.group(1)), int(range_match.group(2))
                        try:
                            context_dt = dateutil_parse(f"{current_month_str} {start_day}").date()
                            correct_year = get_year(context_dt.month)
                            current_date = context_dt.replace(year=correct_year)
                            while current_date.day <= end_day and current_date.month == context_dt.month:
                                parsed_dates.append(current_date)
                                current_date += timedelta(days=1)
                        except ValueError: print(f"Warning: Could not parse range part '{part}' in '{date_str}' with month context '{current_month_str}'")
                    else: print(f"Warning: Unrecognized range format '{part}' in '{date_str}'")
                elif re.fullmatch(r"\d{1,2}", part) and current_month_str:
                    try:
                        dt = dateutil_parse(f"{current_month_str} {part}").date()
                        correct_year = get_year(dt.month)
                        parsed_dates.append(dt.replace(year=correct_year))
                    except ValueError: print(f"Warning: Could not parse day part '{part}' in '{date_str}' with month context '{current_month_str}'")
                else: print(f"Warning: Unhandled part format '{part}' in '{date_str}'")
        elif "onwards" in date_str: # Treat "onwards" as a single day for now
            date_part_str = date_str.replace("onwards", "").strip()
            if re.fullmatch(r"[A-Za-z]{3,}\s+\d{1,2}", date_part_str):
                dt = dateutil_parse(date_part_str).date()
                correct_year = get_year(dt.month)
                parsed_dates.append(dt.replace(year=correct_year))
            else: print(f"Warning: Could not parse 'onwards' date: {date_str}")
        else: print(f"Warning: Unrecognized date string format: {date_str}")
    except Exception as e:
        print(f"Error parsing date string '{date_str}': {e}")
    return sorted(list(set(parsed_dates)))


def process_weekly_constraints(
    target_start_date: date, # This is the start of the PHT week
    target_end_date: date,   # Exclusive end of the PHT week
    calendar_data: Dict[str, Any],
    academic_year_str: str = ACADEMIC_YEAR_STR
) -> Dict[str, Any]:
    print(f"Processing PHT-aware constraints for PHT week: {target_start_date} to {target_end_date}...")
    unavailable_general_slots: List[Dict[str, Any]] = []
    venue_blockages: Dict[str, List[Dict[str, Any]]] = {}
    all_blocked_pht_dates: Set[date] = set()

    try:
        year_start = int(academic_year_str.split('-')[0])
        year_end = int(academic_year_str.split('-')[1])
    except Exception as e:
        raise ValueError(f"Invalid academic_year_str format: {academic_year_str}. Error: {e}")

    is_hectic_week = False
    for period in calendar_data.get('hectic_periods', []):
        parsed_pht_hectic_dates = parse_date_string(period.get('date', ''), year_start, year_end)
        if not parsed_pht_hectic_dates: continue
        min_hectic_pht_date, max_hectic_pht_date = min(parsed_pht_hectic_dates), max(parsed_pht_hectic_dates)
        if target_start_date <= max_hectic_pht_date and target_end_date > min_hectic_pht_date:
            is_hectic_week = True; print(f"Target week overlaps with Hectic Period (PHT): {period.get('name')}"); break

    blockage_categories = ['national_holidays', 'school_holidays_breaks', 'examination_periods']
    for category in blockage_categories:
        for entry in calendar_data.get('unavailable_dates', {}).get(category, []):
            reason = entry.get("event", category.replace('_', ' ').title())
            parsed_pht_dates = parse_date_string(entry.get('date', ''), year_start, year_end)
            for pht_date_obj in parsed_pht_dates:
                if target_start_date <= pht_date_obj < target_end_date:
                    pht_day_block_starts = datetime.combine(pht_date_obj, time.min, tzinfo=PHT_TZ)
                    pht_day_block_ends = datetime.combine(pht_date_obj + timedelta(days=1), time.min, tzinfo=PHT_TZ)
                    unavailable_general_slots.append({
                        'start': pht_day_block_starts.astimezone(timezone.utc),
                        'end': pht_day_block_ends.astimezone(timezone.utc),
                        'reason': reason
                    })
                    all_blocked_pht_dates.add(pht_date_obj)

    exam_period_starts_pht = []
    for entry in calendar_data.get('unavailable_dates', {}).get('examination_periods', []):
        parsed_pht_exam_dates = parse_date_string(entry.get('date', ''), year_start, year_end)
        if parsed_pht_exam_dates: exam_period_starts_pht.append(min(parsed_pht_exam_dates))

    for pht_exam_start_date in sorted(list(set(exam_period_starts_pht))):
        pre_exam_pht_start_day = pht_exam_start_date - timedelta(days=7)
        current_pre_exam_pht_date = pre_exam_pht_start_day
        while current_pre_exam_pht_date < pht_exam_start_date:
            if target_start_date <= current_pre_exam_pht_date < target_end_date and current_pre_exam_pht_date not in all_blocked_pht_dates:
                pht_pre_exam_day_starts = datetime.combine(current_pre_exam_pht_date, time.min, tzinfo=PHT_TZ)
                pht_pre_exam_day_ends = datetime.combine(current_pre_exam_pht_date + timedelta(days=1), time.min, tzinfo=PHT_TZ)
                unavailable_general_slots.append({
                    'start': pht_pre_exam_day_starts.astimezone(timezone.utc),
                    'end': pht_pre_exam_day_ends.astimezone(timezone.utc),
                    'reason': f"PHT Pre-Exam Week Blockage (Exams starting {pht_exam_start_date.strftime('%b %d')})"
                })
                all_blocked_pht_dates.add(current_pre_exam_pht_date)
            current_pre_exam_pht_date += timedelta(days=1)

    current_pht_date_iter = target_start_date
    while current_pht_date_iter < target_end_date:
        pht_day_starts_at = datetime.combine(current_pht_date_iter, time.min, tzinfo=PHT_TZ)
        if pht_day_starts_at.weekday() == 6: # PHT Sunday
            if current_pht_date_iter not in all_blocked_pht_dates:
                pht_sunday_ends_at = datetime.combine(current_pht_date_iter + timedelta(days=1), time.min, tzinfo=PHT_TZ)
                unavailable_general_slots.append({
                    'start': pht_day_starts_at.astimezone(timezone.utc),
                    'end': pht_sunday_ends_at.astimezone(timezone.utc),
                    'reason': "PHT Sunday Blockage"
                })
        
        local_pht_curfew_start_time = time(22, 0) # 10 PM PHT
        local_pht_curfew_end_time = time(6, 0)   # 6 AM PHT
        pht_curfew_starts = datetime.combine(current_pht_date_iter, local_pht_curfew_start_time, tzinfo=PHT_TZ)
        pht_curfew_ends = datetime.combine(current_pht_date_iter + timedelta(days=1), local_pht_curfew_end_time, tzinfo=PHT_TZ)
        unavailable_general_slots.append({
            'start': pht_curfew_starts.astimezone(timezone.utc),
            'end': pht_curfew_ends.astimezone(timezone.utc),
            'reason': "PHT Night Curfew (10PM-6AM PHT)"
        })
        current_pht_date_iter += timedelta(days=1)

    if not is_hectic_week:
        standard_blocks_config = calendar_data.get('scheduling_constraints', {}).get('standard_venue_blockages', {})
        for venue_key, time_ranges_config in standard_blocks_config.items():
            parsed_ranges_for_venue = []
            if not isinstance(time_ranges_config, list): continue
            for tr_config in time_ranges_config:
                if not isinstance(tr_config, dict) or 'start_time' not in tr_config or 'end_time' not in tr_config: continue
                try:
                    start_t, end_t = time.fromisoformat(tr_config['start_time']), time.fromisoformat(tr_config['end_time'])
                    parsed_ranges_for_venue.append({"start": start_t, "end": end_t, "day": tr_config.get("day")})
                except ValueError: print(f"Warning: Could not parse time range {tr_config} for {venue_key}")
            if parsed_ranges_for_venue: venue_blockages[venue_key] = parsed_ranges_for_venue
    
    venue_specific_rules = {"is_hectic_week": is_hectic_week, "blockages": venue_blockages}
    final_unavailable_slots = sorted(unavailable_general_slots, key=lambda x: x['start'])
    print(f"Processed PHT-aware constraints. Hectic Week: {is_hectic_week}. General unavailable UTC slots: {len(final_unavailable_slots)}")
    return {"unavailable_general_slots": final_unavailable_slots, "venue_specific_rules": venue_specific_rules}


async def fetch_ga_data(start_date: date, end_date: date, db: AsyncIOMotorDatabase, week_constraints: Dict[str, Any]) -> Dict[str, Any]:
    pht_week_start_dt = datetime.combine(start_date, time.min, tzinfo=PHT_TZ)
    pht_week_end_dt = datetime.combine(end_date, time.min, tzinfo=PHT_TZ) 
    utc_week_query_start = pht_week_start_dt.astimezone(timezone.utc)
    utc_week_query_end = pht_week_end_dt.astimezone(timezone.utc)
    print(f"Fetching GA data for PHT week: {start_date} to {end_date} (UTC query range: {utc_week_query_start} to {utc_week_query_end})")

    pending_events_cursor = db.events.find({
        "approval_status": "Pending",
        "requested_date": {"$gte": utc_week_query_start, "$lt": utc_week_query_end}
    })
    pending_events = await pending_events_cursor.to_list(length=None)
    for event in pending_events:
        for field in ["requested_date", "requested_time_start", "requested_time_end"]:
            dt = event.get(field)
            if isinstance(dt, datetime):
                if dt.tzinfo is None: event[field] = dt.replace(tzinfo=timezone.utc)
                elif dt.tzinfo != timezone.utc: event[field] = dt.astimezone(timezone.utc)
    pending_event_ids = [event["_id"] for event in pending_events]
    print(f"Found {len(pending_events)} pending events for the PHT week.")

    existing_schedules_cursor = db.schedules.find({
        "is_optimized": False,
        "$and": [
             {"scheduled_start_time": {"$lt": utc_week_query_end}},
             {"scheduled_end_time": {"$gte": utc_week_query_start}}
         ]
    })
    raw_existing_schedules = await existing_schedules_cursor.to_list(length=None)
    existing_schedules = []
    for sched in raw_existing_schedules:
        for field in ["scheduled_start_time", "scheduled_end_time"]:
            dt = sched.get(field)
            if isinstance(dt, datetime):
                if dt.tzinfo is None: sched[field] = dt.replace(tzinfo=timezone.utc)
                elif dt.tzinfo != timezone.utc: sched[field] = dt.astimezone(timezone.utc)
        existing_schedules.append(sched)
    print(f"Found {len(existing_schedules)} existing non-optimized schedules potentially conflicting in PHT week.")

    venues_cursor = db.venues.find({})
    venues_list = await venues_cursor.to_list(length=None)
    venues_dict = {str(v["_id"]): v for v in venues_list}
    print(f"Found {len(venues_list)} venues.")

    all_equipment_docs = await db.equipment.find({}).to_list(None)
    equipment_id_to_name: Dict[str, str] = {}
    equipment_counts: Dict[str, int] = {}
    for item in all_equipment_docs:
        item_id_str, name = str(item["_id"]), item.get("name")
        if name:
            equipment_id_to_name[item_id_str] = name
            equipment_counts[name] = equipment_counts.get(name, 0) + 1 
    print(f"Found {len(all_equipment_docs)} equipment items across {len(equipment_counts)} types.")

    preferences_cursor = db.preferences.find({"event_id": {"$in": pending_event_ids}})
    preferences_list = await preferences_cursor.to_list(length=None)
    prefs_by_event: Dict[str, List[Dict[str, Any]]] = {}
    for pref in preferences_list:
        for field in ["preferred_date", "preferred_time_slot_start", "preferred_time_slot_end"]:
            dt_val = pref.get(field)
            if isinstance(dt_val, datetime):
                if dt_val.tzinfo is None: pref[field] = dt_val.replace(tzinfo=timezone.utc)
                elif dt_val.tzinfo != timezone.utc: pref[field] = dt_val.astimezone(timezone.utc)
            elif isinstance(dt_val, date) and field == "preferred_date": # Handle if preferred_date is stored as Python date
                 pref[field] = datetime.combine(dt_val, time.min, tzinfo=PHT_TZ).astimezone(timezone.utc)
        event_id_str = str(pref['event_id'])
        prefs_by_event.setdefault(event_id_str, []).append(pref)
    print(f"Found preferences for {len(prefs_by_event)} pending events.")

    existing_schedule_event_ids = [s["event_id"] for s in existing_schedules]
    relevant_event_ids = list(set(pending_event_ids + existing_schedule_event_ids))
    requests_by_event_id: Dict[str, List[Dict[str, Any]]] = {}
    if relevant_event_ids:
        event_equipment_cursor = db.event_equipment.find({"event_id": {"$in": relevant_event_ids}})
        all_relevant_event_equipment = await event_equipment_cursor.to_list(None)
        for req in all_relevant_event_equipment:
            evt_id_str = str(req["event_id"])
            req["equipment_id_str"] = str(req["equipment_id"])
            requests_by_event_id.setdefault(evt_id_str, []).append(req)
    print(f"Found equipment requests for {len(requests_by_event_id)} relevant events.")
    
    calendar_config_data = {} # Load fresh for passing, not from global
    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            calendar_config_data = json.load(f)
    except Exception as e:
        print(f"Warning: Could not load calendar config data in fetch_ga_data for report reference: {e}")

    return {
        "pending_events": pending_events, "existing_schedules": existing_schedules,
        "venues": venues_dict, "equipment_counts": equipment_counts,
        "equipment_id_to_name": equipment_id_to_name,
        "equipment_requests_by_event": requests_by_event_id,
        "preferences": prefs_by_event, "week_constraints": week_constraints,
        "target_start_date": start_date, "target_end_date": end_date,
        "_calendar_data_ref": calendar_config_data, 
        "_year_start": int(ACADEMIC_YEAR_STR.split('-')[0]), 
        "_year_end": int(ACADEMIC_YEAR_STR.split('-')[1])
    }

def check_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    return start1 < end2 and end1 > start2

def _check_slot_constraints_for_reason(
    event: Dict[str, Any], venue_id: str, start_time_utc: datetime, end_time_utc: datetime, ga_data: Dict[str, Any]
) -> Optional[str]:
    venues_data = ga_data["venues"]
    constraints = ga_data["week_constraints"]
    unavailable_general: List[Dict[str, Any]] = constraints["unavailable_general_slots"]
    venue_rules = constraints["venue_specific_rules"]
    is_hectic_week = venue_rules["is_hectic_week"]
    venue_blockages_config: Dict[str, List[Dict[str, Any]]] = venue_rules.get("blockages", {})
    target_pht_start_date = ga_data["target_start_date"] # This is a PHT date object
    target_pht_end_date = ga_data["target_end_date"]     # This is a PHT date object
    
    # 1. Check if slot is within the target PHT week
    slot_start_pht_date = start_time_utc.astimezone(PHT_TZ).date()
    if not (target_pht_start_date <= slot_start_pht_date < target_pht_end_date):
        return f"Slot Outside Target PHT Week ({slot_start_pht_date})"

    # 2. General Unavailable Slots (PHT Holidays, PHT Sunday, PHT Night Curfew - all as UTC ranges)
    for constraint_info in unavailable_general:
        unavail_start_utc, unavail_end_utc = constraint_info['start'], constraint_info['end']
        if check_overlap(start_time_utc, end_time_utc, unavail_start_utc, unavail_end_utc):
            return constraint_info.get('reason', "General Unavailability")

    # 3. Venue-Specific Blockages (from config - PHT naive times, converted on-the-fly to UTC)
    if not is_hectic_week:
        venue_doc = venues_data.get(venue_id)
        if not venue_doc: return f"Venue Not Found ({venue_id})"
        
        venue_type_key_base = None
        if "classroom" in venue_doc.get("venue_type", "").lower(): venue_type_key_base = "Classroom"
        elif "uls" in venue_doc.get("name", "").lower(): venue_type_key_base = "ULS"

        if venue_type_key_base:
            event_start_pht = start_time_utc.astimezone(PHT_TZ)
            pht_day_of_week_idx = event_start_pht.weekday()
            blockage_key_type = None
            if pht_day_of_week_idx < 5: blockage_key_type = "_weekday"
            elif pht_day_of_week_idx == 5: blockage_key_type = "_weekend_Sat"
            
            blockage_key = f"{venue_type_key_base}{blockage_key_type}" if blockage_key_type else None

            if blockage_key and blockage_key in venue_blockages_config:
                for block_rule_pht in venue_blockages_config[blockage_key]:
                    rule_specific_pht_day_name = block_rule_pht.get("day")
                    if rule_specific_pht_day_name and event_start_pht.strftime("%A") != rule_specific_pht_day_name:
                        continue
                    block_start_pht_time = block_rule_pht["start"]
                    block_end_pht_time = block_rule_pht["end"]
                    block_start_dt_pht = datetime.combine(event_start_pht.date(), block_start_pht_time, tzinfo=PHT_TZ)
                    block_end_dt_pht = datetime.combine(event_start_pht.date(), block_end_pht_time, tzinfo=PHT_TZ)
                    block_start_dt_utc = block_start_dt_pht.astimezone(timezone.utc)
                    block_end_dt_utc = block_end_dt_pht.astimezone(timezone.utc)
                    if check_overlap(start_time_utc, end_time_utc, block_start_dt_utc, block_end_dt_utc):
                        day_str = f" ({rule_specific_pht_day_name})" if rule_specific_pht_day_name else ""
                        return f"Venue Blockage: {blockage_key}{day_str} ({block_start_pht_time.strftime('%H:%M')}-{block_end_pht_time.strftime('%H:%M')} PHT)"
    
    # 4. Equipment & Capacity (Copied from calculate_fitness, simplified for single event)
    equipment_requests_by_event = ga_data["equipment_requests_by_event"]
    equipment_id_to_name = ga_data["equipment_id_to_name"]
    equipment_counts = ga_data["equipment_counts"]
    event_id_str = str(event["_id"])

    current_event_equip_requests: Dict[str, int] = {}
    if event_id_str in equipment_requests_by_event:
        for req in equipment_requests_by_event[event_id_str]:
            equip_id_str, quantity = req["equipment_id_str"], req.get("quantity", 1)
            if equip_id_str in equipment_id_to_name:
                equip_name = equipment_id_to_name[equip_id_str]
                if equip_name not in equipment_counts: return f"Equipment '{equip_name}' Not Found in Inventory"
                current_event_equip_requests[equip_name] = current_event_equip_requests.get(equip_name, 0) + quantity
            else: return f"Requested Equipment ID '{equip_id_str}' Not Found"
    
    for equip_name, requested_qty in current_event_equip_requests.items():
        if requested_qty > equipment_counts.get(equip_name, 0):
            return f"Equipment Unavailable: '{equip_name}' (Needs {requested_qty}, Has {equipment_counts.get(equip_name, 0)})"

    venue_doc_cap = venues_data.get(venue_id) # Re-fetch for safety
    if venue_doc_cap:
        capacity, attendees = venue_doc_cap.get("occupancy"), event.get("estimated_attendees")
        if capacity is not None and attendees is not None and attendees > capacity:
            return f"Capacity Exceeded (Needs {attendees}, Venue Has {capacity})"
    else: return f"Venue Data Error For Capacity Check ({venue_id})" # Should have been caught by venue not found
            
    return None # Slot is valid according to these checks

def _run_post_mortem_analysis(unscheduled_event_ids: List[ObjectId], ga_data: Dict[str, Any]) -> Dict[str, List[str]]:
    print(f"\n--- Running Post-Mortem Analysis for {len(unscheduled_event_ids)} Unscheduled Events ---")
    analysis_results: Dict[str, List[str]] = {}
    if not unscheduled_event_ids: return analysis_results

    pending_events_dict = {str(e["_id"]): e for e in ga_data["pending_events"]}
    venues = list(ga_data["venues"].values())
    if not venues:
        for event_obj_id in unscheduled_event_ids: analysis_results[str(event_obj_id)] = ["Post-mortem: No venues available."]
        return analysis_results

    target_pht_start_date = ga_data["target_start_date"]
    target_pht_end_date = ga_data["target_end_date"]
    
    # Try a few representative PHT time slots per day
    pht_times_to_check = [time(9,0), time(10,30), time(13,0), time(14,30), time(16,0)]

    for event_obj_id in unscheduled_event_ids:
        event_id_str = str(event_obj_id)
        event = pending_events_dict.get(event_id_str)
        if not event: continue

        print(f"Analyzing unscheduled event: {event.get('event_name', event_id_str)}")
        conflict_reasons: Set[str] = set()
        
        duration = timedelta(hours=1.5) 
        if event.get("requested_time_start") and event.get("requested_time_end") and (event["requested_time_end"] > event["requested_time_start"]):
             duration = event["requested_time_end"] - event["requested_time_start"]

        current_pht_date = target_pht_start_date
        slots_checked_for_event = 0
        while current_pht_date < target_pht_end_date:
            if current_pht_date.weekday() == 6: # Skip PHT Sunday for attempts by default
                current_pht_date += timedelta(days=1); continue
            for venue in venues:
                venue_id_str = str(venue["_id"])
                for pht_time_component in pht_times_to_check:
                    if time(22,0) <= pht_time_component or pht_time_component < time(6,0) : continue # Skip PHT curfew times

                    attempt_pht_start = datetime.combine(current_pht_date, pht_time_component, tzinfo=PHT_TZ)
                    attempt_utc_start = attempt_pht_start.astimezone(timezone.utc)
                    attempt_utc_end = attempt_utc_start + duration
                    
                    # Quick check if end time spills too late into PHT night based on UTC conversion
                    attempt_pht_end_check = attempt_utc_end.astimezone(PHT_TZ)
                    if attempt_pht_end_check.time() > time(22,0) and attempt_pht_end_check.time() != time(0,0) : continue
                    if attempt_pht_end_check.date() > attempt_pht_start.date() and attempt_pht_end_check.time() > time(6,0): continue
                    
                    slots_checked_for_event +=1
                    reason = _check_slot_constraints_for_reason(event, venue_id_str, attempt_utc_start, attempt_utc_end, ga_data)
                    if reason: conflict_reasons.add(reason)
            current_pht_date += timedelta(days=1)

        if slots_checked_for_event == 0:
             analysis_results[event_id_str] = ["No valid PHT daytime slots could be checked (review target week/PHT rules)."]
        elif not conflict_reasons:
             analysis_results[event_id_str] = ["No specific constraint conflicts found in sampled PHT daytime slots. Failure may be due to conflicts with other chosen events or GA not finding a solution."]
        else:
             grouped_reasons: Dict[str, Set[str]] = {}
             for r in conflict_reasons:
                 rtype = r.split(':')[0] if ':' in r else r
                 grouped_reasons.setdefault(rtype, set()).add(r)
             
             temp_analysis = ["Potential blocking constraints identified (based on sampled PHT daytime slots):"]
             for reason_type in sorted(grouped_reasons.keys()):
                 temp_analysis.append(f"  {reason_type}:")
                 for specific_reason in sorted(list(grouped_reasons[reason_type])):
                      temp_analysis.append(f"    - {specific_reason}")
             analysis_results[event_id_str] = temp_analysis
    return analysis_results

def calculate_fitness(chromosome: Chromosome, ga_data: Dict[str, Any], weights: Dict[str, float]) -> FitnessResult:
    hard_constraint_violations = 0
    soft_constraint_score = 0.0
    
    venues_data = ga_data["venues"]
    constraints = ga_data["week_constraints"]
    unavailable_general: List[Dict[str, Any]] = constraints["unavailable_general_slots"]
    venue_rules = constraints["venue_specific_rules"]
    venue_blockages_config: Dict[str, List[Dict[str, Any]]] = venue_rules.get("blockages", {})
    is_hectic_week = venue_rules["is_hectic_week"]
    pending_events_dict = {str(e["_id"]): e for e in ga_data["pending_events"]}
    prefs_by_event = ga_data["preferences"]
    equipment_requests = ga_data["equipment_requests_by_event"]
    equipment_id_to_name = ga_data["equipment_id_to_name"]
    equipment_counts = ga_data["equipment_counts"]

    active_slots_by_venue: Dict[str, List[Tuple[datetime, datetime, str]]] = {}
    for event_id_str, slot_data in chromosome.items():
        if slot_data:
            venue_id, start_time_utc, end_time_utc = slot_data
            active_slots_by_venue.setdefault(venue_id, []).append((start_time_utc, end_time_utc, event_id_str))
    for existing in ga_data["existing_schedules"]:
        venue_id, start_time_utc, end_time_utc, event_id = str(existing["venue_id"]), existing["scheduled_start_time"], existing["scheduled_end_time"], str(existing["event_id"])
        active_slots_by_venue.setdefault(venue_id, []).append((start_time_utc, end_time_utc, f"existing_{event_id}"))

    for event_id_str, slot_data in chromosome.items():
        if not slot_data: continue
        venue_id, start_time_utc, end_time_utc = slot_data
        original_event = pending_events_dict.get(event_id_str)
        if not original_event: hard_constraint_violations += 1; continue
        
        current_event_violations = 0
        # 1. Check against unavailable_general_slots (PHT rules as UTC ranges)
        for constraint_info in unavailable_general:
            if check_overlap(start_time_utc, end_time_utc, constraint_info['start'], constraint_info['end']):
                current_event_violations += 1; break
        if current_event_violations > 0: hard_constraint_violations += 1; continue

        # 2. Venue-Specific Blockages
        if not is_hectic_week:
            venue_doc = venues_data.get(venue_id)
            if venue_doc:
                venue_type_key_base = ("Classroom" if "classroom" in venue_doc.get("venue_type", "").lower() else 
                                       "ULS" if "uls" in venue_doc.get("name", "").lower() else None)
                if venue_type_key_base:
                    event_start_pht = start_time_utc.astimezone(PHT_TZ)
                    blockage_key_type = ("_weekday" if event_start_pht.weekday() < 5 else 
                                         "_weekend_Sat" if event_start_pht.weekday() == 5 else None)
                    blockage_key = f"{venue_type_key_base}{blockage_key_type}" if blockage_key_type else None
                    if blockage_key and blockage_key in venue_blockages_config:
                        for block_rule_pht in venue_blockages_config[blockage_key]:
                            rule_day, block_start_t, block_end_t = block_rule_pht.get("day"), block_rule_pht["start"], block_rule_pht["end"]
                            if rule_day and event_start_pht.strftime("%A") != rule_day: continue
                            block_s_pht_dt = datetime.combine(event_start_pht.date(), block_start_t, tzinfo=PHT_TZ)
                            block_e_pht_dt = datetime.combine(event_start_pht.date(), block_end_t, tzinfo=PHT_TZ)
                            if check_overlap(start_time_utc, end_time_utc, block_s_pht_dt.astimezone(timezone.utc), block_e_pht_dt.astimezone(timezone.utc)):
                                current_event_violations += 1; break
                        if current_event_violations > 0: break 
            else: current_event_violations += 1
        if current_event_violations > 0: hard_constraint_violations += 1; continue

        # 3. Conflicts with Other Slots
        if venue_id in active_slots_by_venue:
            for other_s, other_e, other_id_ctx in active_slots_by_venue[venue_id]:
                if other_id_ctx != event_id_str and check_overlap(start_time_utc, end_time_utc, other_s, other_e):
                    current_event_violations += 1; break
        if current_event_violations > 0: hard_constraint_violations += 1; continue
        
        # 4. Equipment Conflicts
        concurrent_events_ids = {event_id_str}
        for _, slots_list in active_slots_by_venue.items():
            for s_time, e_time, id_ctx in slots_list:
                if id_ctx != event_id_str and check_overlap(start_time_utc, end_time_utc, s_time, e_time):
                    concurrent_events_ids.add(id_ctx.replace("existing_", ""))
        
        equip_needed_now: Dict[str, int] = {}
        for con_event_id in concurrent_events_ids:
            for req in equipment_requests.get(con_event_id, []):
                equip_name = equipment_id_to_name.get(req["equipment_id_str"])
                if equip_name: equip_needed_now[equip_name] = equip_needed_now.get(equip_name, 0) + req.get("quantity", 1)
        
        for equip_name, needed_qty in equip_needed_now.items():
            if needed_qty > equipment_counts.get(equip_name, 0):
                current_event_violations += 1; break
        if current_event_violations > 0: hard_constraint_violations += 1; continue

        # --- Soft Constraint Scoring ---
        current_event_score = weights.get('base_score_multiplier', 10.0)
        event_req_date_pht, slot_start_pht = original_event["requested_date"].astimezone(PHT_TZ).date(), start_time_utc.astimezone(PHT_TZ)
        
        venue_pref_score = 0
        if venue_id == str(original_event.get("requested_venue_id")): venue_pref_score = weights.get("venue_preference_match", 50.0)
        else:
            for pref in prefs_by_event.get(event_id_str, []):
                if venue_id == str(pref.get("preferred_venue_id")): venue_pref_score = weights.get("venue_preference_match", 50.0) * 0.8; break
        current_event_score += venue_pref_score
        
        datetime_match_score = 0
        if event_req_date_pht == slot_start_pht.date():
            datetime_match_score += weights.get("date_match", 20.0) * 0.5
            if check_overlap(start_time_utc, end_time_utc, original_event["requested_time_start"], original_event["requested_time_end"]):
                datetime_match_score += weights.get("timeslot_match", 30.0) * 0.5
        else:
            for pref in prefs_by_event.get(event_id_str, []):
                pref_date_utc, current_pref_dt_score = pref.get("preferred_date"), 0.0
                if pref_date_utc and isinstance(pref_date_utc, datetime) and pref_date_utc.astimezone(PHT_TZ).date() == slot_start_pht.date():
                    current_pref_dt_score += weights.get("date_match", 20.0) * 0.5
                    pref_s_utc, pref_e_utc = pref.get("preferred_time_slot_start"), pref.get("preferred_time_slot_end")
                    if pref_s_utc and pref_e_utc and check_overlap(start_time_utc, end_time_utc, pref_s_utc, pref_e_utc):
                        current_pref_dt_score += weights.get("timeslot_match", 30.0) * 0.5
                datetime_match_score = max(datetime_match_score, current_pref_dt_score * 0.8)
        current_event_score += datetime_match_score
        
        if is_hectic_week:
            cal_data, yr_s, yr_e = ga_data["_calendar_data_ref"], ga_data["_year_start"], ga_data["_year_end"]
            for period in cal_data.get('hectic_periods', []):
                hectic_dates = parse_date_string(period.get('date', ''), yr_s, yr_e)
                if hectic_dates and min(hectic_dates) <= event_req_date_pht <= max(hectic_dates):
                    current_event_score += weights.get("hectic_week_priority_bonus", 100.0); break
        
        venue_doc_cap_check = venues_data.get(venue_id)
        if venue_doc_cap_check:
            cap, attendees = venue_doc_cap_check.get("occupancy"), original_event.get("estimated_attendees")
            if cap is not None and attendees is not None and attendees > cap:
                current_event_score += weights.get("capacity_fit_penalty", -10.0) * (1 + (attendees - cap) / max(1, cap))
        soft_constraint_score += current_event_score
            
    return (soft_constraint_score - (hard_constraint_violations * weights.get('hard_constraint_penalty', 10000.0)), hard_constraint_violations)

def initialize_population(size: int, ga_data: Dict[str, Any]) -> List[Chromosome]:
    population = []
    pending_events = ga_data["pending_events"]
    venues = list(ga_data["venues"].values())
    target_pht_start_date = ga_data["target_start_date"]

    if not venues or not pending_events: return [{} for _ in range(size)]

    for _ in range(size):
        chromosome: Chromosome = {}
        for event in pending_events:
            event_id_str = str(event["_id"])
            if random.random() < 0.9:
                chosen_venue_id_str = str(random.choice(venues)["_id"])
                slot_start_utc, slot_end_utc = None, None
                
                if random.random() < 0.5:
                    req_start_utc, req_end_utc = event["requested_time_start"], event["requested_time_end"]
                    req_start_pht = req_start_utc.astimezone(PHT_TZ)
                    if target_pht_start_date <= req_start_pht.date() < ga_data["target_end_date"] and \
                       req_start_pht.weekday() != 6 and \
                       not (time(22,0) <= req_start_pht.time() or req_start_pht.time() < time(6,0)):
                        slot_start_utc, slot_end_utc = req_start_utc, req_end_utc

                if not slot_start_utc:
                    for _ in range(20):
                        day_offset = random.randint(0, (ga_data["target_end_date"] - target_pht_start_date).days -1 )
                        rand_pht_date = target_pht_start_date + timedelta(days=day_offset)
                        if rand_pht_date.weekday() == 6: continue

                        rand_pht_hour, rand_pht_minute = random.randint(6, 21), random.choice([0, 15, 30, 45])
                        temp_pht_start = datetime.combine(rand_pht_date, time(rand_pht_hour, rand_pht_minute), tzinfo=PHT_TZ)
                        if time(22,0) <= temp_pht_start.time() or temp_pht_start.time() < time(6,0): continue

                        duration = (event["requested_time_end"] - event["requested_time_start"]) \
                                   if (event.get("requested_time_start") and event.get("requested_time_end") and \
                                       (event["requested_time_end"] > event["requested_time_start"])) \
                                   else timedelta(hours=1.5)
                        temp_pht_end = temp_pht_start + duration
                        
                        if (temp_pht_end.time() > time(22,0) and temp_pht_end.time() != time(0,0)) or \
                           (temp_pht_end.date() > temp_pht_start.date() and temp_pht_end.time() > time(6,0)): continue
                        
                        slot_start_utc, slot_end_utc = temp_pht_start.astimezone(timezone.utc), temp_pht_end.astimezone(timezone.utc)
                        break
                    else: chromosome[event_id_str] = None; continue
                chromosome[event_id_str] = (chosen_venue_id_str, slot_start_utc, slot_end_utc)
            else: chromosome[event_id_str] = None
        population.append(chromosome)
    return population

def selection(population: List[Chromosome], fitness_results: List[FitnessResult], k: int) -> Chromosome:
    if not population or not fitness_results or len(population) != len(fitness_results):
         return random.choice(population) if population else {} # Handle empty population
    actual_k = min(k, len(population))
    if actual_k <= 0: return population[0] if population else {}
    tournament_indices = random.sample(range(len(population)), actual_k)
    # Use fitness_results[i][0] which is the fitness score
    tournament_contenders = [(population[i], fitness_results[i][0]) for i in tournament_indices] 
    return max(tournament_contenders, key=lambda x: x[1])[0]

def crossover(parent1: Chromosome, parent2: Chromosome, ga_data: Dict[str, Any], rate: float) -> Tuple[Chromosome, Chromosome]:
    if random.random() >= rate: return parent1.copy(), parent2.copy()
    child1, child2 = {}, {}
    # Ensure we iterate over all possible event IDs defined in pending_events
    event_ids_from_data = [str(e["_id"]) for e in ga_data["pending_events"]]
    for event_id_str in event_ids_from_data:
        slot1, slot2 = parent1.get(event_id_str), parent2.get(event_id_str)
        if random.random() < 0.5: child1[event_id_str], child2[event_id_str] = slot1, slot2
        else: child1[event_id_str], child2[event_id_str] = slot2, slot1
    return child1, child2

def mutate(chromosome: Chromosome, ga_data: Dict[str, Any], rate: float) -> Chromosome:
    mutated_chromosome = chromosome.copy()
    pending_events = ga_data["pending_events"]
    venues = list(ga_data["venues"].values())
    target_pht_start_date = ga_data["target_start_date"]

    if not venues: return mutated_chromosome

    for event_data in pending_events:
        event_id_str = str(event_data["_id"])
        if random.random() < rate:
            new_slot_utc = None
            for _ in range(20): # More attempts for better random slot
                chosen_venue_id_str = str(random.choice(venues)["_id"])
                day_offset = random.randint(0, (ga_data["target_end_date"] - target_pht_start_date).days - 1)
                rand_pht_date = target_pht_start_date + timedelta(days=day_offset)
                if rand_pht_date.weekday() == 6: continue

                rand_pht_hour, rand_pht_minute = random.randint(6, 21), random.choice([0, 15, 30, 45])
                temp_pht_start = datetime.combine(rand_pht_date, time(rand_pht_hour, rand_pht_minute), tzinfo=PHT_TZ)
                if time(22,0) <= temp_pht_start.time() or temp_pht_start.time() < time(6,0): continue

                duration = (event_data["requested_time_end"] - event_data["requested_time_start"]) \
                           if (event_data.get("requested_time_start") and event_data.get("requested_time_end") and \
                               (event_data["requested_time_end"] > event_data["requested_time_start"])) \
                           else timedelta(hours=1.5)
                temp_pht_end = temp_pht_start + duration
                
                if (temp_pht_end.time() > time(22,0) and temp_pht_end.time() != time(0,0)) or \
                   (temp_pht_end.date() > temp_pht_start.date() and temp_pht_end.time() > time(6,0)): continue
                
                new_slot_start_utc, new_slot_end_utc = temp_pht_start.astimezone(timezone.utc), temp_pht_end.astimezone(timezone.utc)
                new_slot_utc = (chosen_venue_id_str, new_slot_start_utc, new_slot_end_utc)
                break
            mutated_chromosome[event_id_str] = new_slot_utc
    return mutated_chromosome

async def optimize_weekly_schedule(
    start_date: date, end_date: date, db: AsyncIOMotorDatabase, weights: Dict[str, float],
    population_size: int = DEFAULT_POPULATION_SIZE, max_generations: int = DEFAULT_MAX_GENERATIONS,
    mutation_rate: float = DEFAULT_MUTATION_RATE, crossover_rate: float = DEFAULT_CROSSOVER_RATE,
    tournament_size: int = DEFAULT_TOURNAMENT_SIZE
) -> Optional[Tuple[List[Dict[str, Any]], List[ObjectId], Dict[str, Any]]]:
    print(f"\n=== Starting GA Optimization for PHT Week: {start_date} to {end_date} ===")
    report_data: Dict[str, Any] = {
        "ga_params": {"pop": population_size, "gens": max_generations, "mut": mutation_rate, "cross": crossover_rate},
        "summary": "Optimization started.", "final_fitness": -float('inf'), "final_violations": float('inf')
    }
    try:
        with open(CONFIG_FILE_PATH, 'r') as f: calendar_data = json.load(f)
    except Exception as e:
        report_data["summary"] = f"Error loading config: {e}"; return ([], [], report_data)

    try:
        week_constraints = process_weekly_constraints(start_date, end_date, calendar_data)
        ga_data = await fetch_ga_data(start_date, end_date, db, week_constraints)
        pending_events_from_ga_data = ga_data.get("pending_events", []) # Define here for broader scope
        report_data.update({
            "input_event_count": len(pending_events_from_ga_data),
            "is_hectic_week": week_constraints["venue_specific_rules"]["is_hectic_week"],
        })
        
        pht_week_start_dt_for_report = datetime.combine(start_date, time.min, tzinfo=PHT_TZ)
        utc_week_start_for_report = pht_week_start_dt_for_report.astimezone(timezone.utc)
        utc_week_end_for_report = datetime.combine(end_date, time.min, tzinfo=PHT_TZ).astimezone(timezone.utc)
        
        relevant_constraints_report = sorted(
            [c for c in week_constraints.get("unavailable_general_slots", []) if c['start'] < utc_week_end_for_report and c['end'] > utc_week_start_for_report],
            key=lambda x: x['start']
        )
        report_data["active_general_constraints"] = [
            f"{c.get('reason', 'Constraint')}: {c['start'].strftime('%Y-%m-%d %H:%M UTC')} - {c['end'].strftime('%Y-%m-%d %H:%M UTC')}"
            for c in relevant_constraints_report
        ]
        if not report_data["is_hectic_week"]:
             report_data["active_venue_blockages"] = {
                 k: [f"{b['start'].strftime('%H:%M')}-{b['end'].strftime('%H:%M')}" + (f" ({b['day']})" if b.get('day') else "") for b in blks]
                 for k, blks in week_constraints["venue_specific_rules"].get("blockages", {}).items()
             }
        if not pending_events_from_ga_data: # Use the defined variable
            report_data["summary"] = "No pending events for this week."; return ([], [], report_data)
    except Exception as e:
        print(f"Error during data prep for GA: {e}") # More specific print
        report_data["summary"] = f"Error during data prep: {e}"; return ([], [], report_data)

    all_input_event_ids_obj = [e["_id"] for e in pending_events_from_ga_data] # Use the defined variable
    pending_events_dict = {str(e["_id"]): e for e in pending_events_from_ga_data} # Define here

    population = initialize_population(population_size, ga_data)
    if not population and pending_events_from_ga_data:
        report_data["summary"] = "Failed to initialize population."; 
        report_data["unscheduled_event_analysis"] = _run_post_mortem_analysis(all_input_event_ids_obj, ga_data)
        return ([], all_input_event_ids_obj, report_data)

    best_fitness_overall, best_chromosome_overall, best_violation_count = -float('inf'), None, float('inf')

    for gen in range(max_generations):
        fitness_results = [calculate_fitness(chrom, ga_data, weights) for chrom in population]
        current_best_idx = max(range(len(fitness_results)), key=lambda i: fitness_results[i][0]) # Ensure fitness_results not empty
        current_best_fitness, current_best_violations = fitness_results[current_best_idx]

        if current_best_violations < best_violation_count or \
           (current_best_violations == best_violation_count and current_best_fitness > best_fitness_overall):
            best_fitness_overall, best_chromosome_overall, best_violation_count = current_best_fitness, population[current_best_idx].copy(), current_best_violations
        
        print(f"Gen {gen+1}/{max_generations} - Best Fitness: {best_fitness_overall:.2f}, Violations: {best_violation_count}")
        if best_violation_count == 0 and best_fitness_overall > 0: pass

        new_population = [best_chromosome_overall.copy()] if best_chromosome_overall and population_size > 0 else [] # Ensure pop size > 0 for elitism
        while len(new_population) < population_size:
            parent1, parent2 = selection(population, fitness_results, tournament_size), selection(population, fitness_results, tournament_size)
            child1, child2 = crossover(parent1, parent2, ga_data, crossover_rate)
            new_population.extend([mutate(child1, ga_data, mutation_rate), mutate(child2, ga_data, mutation_rate)][:population_size-len(new_population)])
        population = new_population

    report_data.update({"final_fitness": best_fitness_overall, "final_violations": best_violation_count})

    if not best_chromosome_overall:
        report_data["summary"] = "GA did not find a suitable schedule (no best chromosome found)."
        report_data["unscheduled_event_analysis"] = _run_post_mortem_analysis(all_input_event_ids_obj, ga_data)
        return ([], all_input_event_ids_obj, report_data)

    final_fitness_check, final_violations_check = calculate_fitness(best_chromosome_overall, ga_data, weights)
    report_data.update({"final_fitness_verified": final_fitness_check, "final_violations_verified": final_violations_check})

    final_schedule_entries, unscheduled_event_ids_obj = [], []
    if final_violations_check > 0:
        report_data["summary"] = f"Best solution found by GA still has {final_violations_check} hard violations. All events treated as unscheduled."
        unscheduled_event_ids_obj = all_input_event_ids_obj # All are unscheduled
    else:
        scheduled_ids_in_best_obj = set()
        for event_id_str, slot in best_chromosome_overall.items():
            if slot:
                venue_id, start_time, end_time = slot
                original_event = pending_events_dict.get(event_id_str)
                if original_event:
                    final_schedule_entries.append({
                        "event_id": original_event["_id"], "venue_id": ObjectId(venue_id),
                        "organization_id": original_event["organization_id"],
                        "scheduled_start_time": start_time, "scheduled_end_time": end_time,
                        "is_optimized": True,
                    })
                    scheduled_ids_in_best_obj.add(original_event["_id"])
        unscheduled_event_ids_obj = list(set(all_input_event_ids_obj) - scheduled_ids_in_best_obj)
        report_data["summary"] = f"GA proposed schedule for {len(final_schedule_entries)} events. Unscheduled: {len(unscheduled_event_ids_obj)}."

    if unscheduled_event_ids_obj: # Always run post-mortem if any events are unscheduled
        report_data["unscheduled_event_analysis"] = _run_post_mortem_analysis(unscheduled_event_ids_obj, ga_data)
    
    return (final_schedule_entries, unscheduled_event_ids_obj, report_data)