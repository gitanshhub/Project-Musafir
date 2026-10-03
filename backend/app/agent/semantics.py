"""
Project Musafir — Centralized Semantics Engine (Step 12.6)
Implements timezone-aware date resolution (Asia/Kolkata default),
strict day vs night calculation precedence, and budget semantics.
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python fallback if zoneinfo is unavailable
    ZoneInfo = None

DEFAULT_TIMEZONE = "Asia/Kolkata"


def get_current_date(tz_name: str = DEFAULT_TIMEZONE) -> date:
    """Returns current date in the specified timezone (default Asia/Kolkata)."""
    if ZoneInfo:
        try:
            tz = ZoneInfo(tz_name)
            return datetime.now(tz).date()
        except Exception:
            pass
    # UTC fallback
    return datetime.now(timezone.utc).date()


def calculate_nights(
    number_of_days: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    explicit_nights: Optional[int] = None,
) -> int:
    """
    Calculates number of hotel nights according to strict precedence:
    1. Explicit nights supplied by user (e.g. "4 nights") -> exact nights
    2. Explicit check-in and check-out dates -> (end_date - start_date).days
    3. Calendar days -> max(0, number_of_days - 1)
       - 1 day -> 0 nights (day trip)
       - 2 days -> 1 night
       - 5 days -> 4 nights
    4. Fallback -> 0 nights
    """
    if explicit_nights is not None and explicit_nights >= 0:
        return explicit_nights

    if start_date is not None and end_date is not None and end_date >= start_date:
        return (end_date - start_date).days

    if number_of_days is not None and number_of_days >= 1:
        return max(0, number_of_days - 1)

    return 0


def calculate_nightly_hotel_budget(
    total_hotel_budget: Optional[float],
    number_of_days: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    explicit_nights: Optional[int] = None,
) -> Optional[float]:
    """
    Derives nightly hotel price ceiling from total hotel budget.
    CRITICAL RULE: Never derive nightly hotel budget until the system knows
    actual nights (explicit nights, start & end dates, or number_of_days).
    If nights == 0 (e.g. 1-day day trip), returns None.
    """
    if total_hotel_budget is None or total_hotel_budget <= 0:
        return None

    # Do NOT derive nightly budget if no duration or nights are known
    if (
        explicit_nights is None
        and not (start_date is not None and end_date is not None)
        and number_of_days is None
    ):
        return None

    nights = calculate_nights(
        number_of_days=number_of_days,
        start_date=start_date,
        end_date=end_date,
        explicit_nights=explicit_nights,
    )
    if nights <= 0:
        return None

    return round(float(total_hotel_budget) / float(nights), 2)


def parse_currency_amount(text: str) -> Optional[float]:
    """
    Parses currency amounts from text.
    Handles '3500', 'total 3500', '₹20,000', 'around 5k', '3.5k', '4000/night'.
    CRITICAL: Never parses dates or date ranges (e.g. '24 sept - 29 sept' or '24 sept')
    as generic numbers or budgets.
    """
    if not text or not str(text).strip():
        return None

    clean = str(text).strip().lower()

    # If the text represents a date or date range or contains month words, reject as currency
    date_indicators = [
        "jan", "january", "feb", "february", "mar", "march", "apr", "april",
        "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept", "september",
        "oct", "october", "nov", "november", "dec", "december",
        "tomorrow", "yesterday", "weekend", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday",
    ]
    if any(re.search(rf"\b{di}\b", clean) for di in date_indicators):
        return None

    # Slashes or dashes like 24/09 or 24-09
    if re.search(r"\b\d{1,2}[/-]\d{1,2}\b", clean):
        return None

    # Check for explicit currency cues
    has_explicit_currency = bool(re.search(r"[₹$]|rs|inr|rupees|budget|total|night|k\b", clean))

    # Clean text
    clean_stripped = re.sub(r"[₹$,]", "", clean)
    clean_stripped = re.sub(
        r"(?:(?<=\d)|(?<=\s)|\b)(?:rs\.?|inr|rupees|total|around|about|approx|approx\.|budget|only|per night|/night)\b",
        " ",
        clean_stripped,
        flags=re.IGNORECASE,
    )
    clean_stripped = clean_stripped.strip()

    # 1. Match '1.5L' or '2 lakh' or '1 lac'
    lakh_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:l|lac|lakh|lakhs)\b", clean_stripped)
    if lakh_match:
        try:
            return float(lakh_match.group(1)) * 100000.0
        except (ValueError, TypeError):
            pass

    # 2. Match '5k' or '3.5k'
    k_match = re.search(r"(\d+(?:\.\d+)?)\s*k\b", clean_stripped)
    if k_match:
        try:
            return float(k_match.group(1)) * 1000.0
        except (ValueError, TypeError):
            pass

    # 3. Match standard number
    num_match = re.search(r"\b(\d+(?:\.\d+)?)\b", clean_stripped)
    if num_match:
        try:
            val = float(num_match.group(1))
            # If val is small (<= 31) and NO explicit currency cue was provided,
            # this is likely a day or ordinal or duration, NOT a budget of ₹24!
            if val <= 31 and not has_explicit_currency:
                return None
            return val if val > 0 else None
        except (ValueError, TypeError):
            pass

    return None


def parse_duration_days(text: str) -> Optional[int]:
    """
    Parses duration in days from user utterance.
    Handles '5', '5 days', '3-day', 'for 7 days', 'a week', 'weekend', 'for a day'.
    CRITICAL: Never extracts dates like '24 Sept' or '24/09' as durations.
    """
    if not text or not str(text).strip():
        return None

    clean = str(text).strip().lower()

    # Reject if date indicators present (except 'weekend')
    date_indicators = [
        "jan", "january", "feb", "february", "mar", "march", "apr", "april",
        "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept", "september",
        "oct", "october", "nov", "november", "dec", "december",
        "tomorrow", "yesterday", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday",
    ]
    if clean in {"weekend", "this weekend", "a weekend"}:
        return 2

    if any(re.search(rf"\b{di}\b", clean) for di in date_indicators):
        return None

    if re.search(r"\b\d{1,2}[/-]\d{1,2}\b", clean):
        return None

    if clean in {"a week", "one week", "1 week"}:
        return 7

    if re.search(r"\b(?:a|1|one)\s+week\b", clean):
        return 7

    # "for a day", "for one day", "for 1 day", "just for the day", "day trip"
    if re.search(r"\b(?:day\s+trip|single\s+day|for\s+a\s+day|for\s+one\s+day|just\s+for\s+the\s+day|for\s+1\s+day)\b", clean):
        return 1

    # "X days" or "X day" or "X-day"
    days_match = re.search(r"\b(\d+)\s*[- ]*\s*days?\b", clean)
    if days_match:
        return int(days_match.group(1))

    # Single standalone integer if message is very short (e.g. "5", "for 5", "no make that 7", "actually 7")
    standalone_match = re.match(
        r"^(?:no[, ]+)?(?:for|just|about|actually|make that)?\s*(\d{1,2})\s*(?:days?)?$",
        clean,
    )
    if standalone_match:
        val = int(standalone_match.group(1))
        if 1 <= val <= 90:
            return val

    return None


def parse_duration_nights(text: str) -> Optional[int]:
    """
    Parses explicit nights duration (e.g. '4 nights', '5 nights stay').
    """
    if not text or not str(text).strip():
        return None

    clean = str(text).strip().lower()
    nights_match = re.search(r"\b(\d+)\s*[- ]*\s*nights?\b", clean)
    if nights_match:
        return int(nights_match.group(1))

    return None


def resolve_relative_date(
    text: str,
    reference_date: Optional[date] = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> Optional[date]:
    """
    Resolves relative or natural language dates into an exact calendar date.
    All calculations use reference_date (defaults to today in Asia/Kolkata).
    Handles:
    - 'today', 'tomorrow', 'day after tomorrow'
    - 'next friday', 'this saturday', 'this weekend'
    - '24 September', '24th September', '24 Sep', '24 sept'
    - 'Sep 24', 'September 24th'
    - '24/09', '24-09'
    - conversational prefixes: 'start tomorrow', 'starting 24 Sept', "I'll start on 24th September"
    """
    if not text or not str(text).strip():
        return None

    ref = reference_date or get_current_date(tz_name)
    clean = str(text).strip().lower()

    # Strip conversational prefixes/suffixes
    clean = re.sub(
        r"^(?:start(?:ing)?\s+on\s+|start(?:ing)?\s+from\s+|start(?:ing)?\s+|from\s+|on\s+|i'll\s+start\s+on\s+|ill\s+start\s+on\s+)",
        "",
        clean,
    ).strip()
    clean = re.sub(r"\s+(?:onwards|please)$", "", clean).strip()

    # 1. Direct keywords
    if clean in {"today"}:
        return ref
    if clean in {"tomorrow", "from tomorrow", "start tomorrow"}:
        return ref + timedelta(days=1)
    if clean in {"day after tomorrow", "day after"}:
        return ref + timedelta(days=2)

    # 2. Weekdays
    weekday_map = {
        "monday": 0, "mon": 0,
        "tuesday": 1, "tue": 1, "tues": 1,
        "wednesday": 2, "wed": 2,
        "thursday": 3, "thu": 3, "thurs": 3,
        "friday": 4, "fri": 4,
        "saturday": 5, "sat": 5,
        "sunday": 6, "sun": 6,
    }

    # "this weekend" -> upcoming Saturday
    if clean in {"this weekend", "weekend", "coming weekend"}:
        days_ahead = (5 - ref.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return ref + timedelta(days=days_ahead)

    # "next weekend" -> Saturday of next week
    if clean in {"next weekend"}:
        days_ahead = (5 - ref.weekday()) % 7 + 7
        return ref + timedelta(days=days_ahead)

    for day_name, day_num in weekday_map.items():
        if clean in {day_name, f"this {day_name}", f"on {day_name}", f"coming {day_name}"}:
            days_ahead = (day_num - ref.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return ref + timedelta(days=days_ahead)

        if clean in {f"next {day_name}"}:
            days_ahead = (day_num - ref.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            elif day_num > ref.weekday():
                days_ahead += 7
            return ref + timedelta(days=days_ahead)

    # 3. ISO Date format: YYYY-MM-DD
    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", clean)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    # 4. Day/Month numerical format: DD/MM or DD-MM (e.g. "24/09", "24-09", "24/9")
    slash_match = re.match(r"^(\d{1,2})[/-](\d{1,2})$", clean)
    if slash_match:
        d_val = int(slash_match.group(1))
        m_val = int(slash_match.group(2))
        if 1 <= d_val <= 31 and 1 <= m_val <= 12:
            y_val = (
                ref.year
                if (m_val > ref.month or (m_val == ref.month and d_val >= ref.day))
                else ref.year + 1
            )
            try:
                return date(y_val, m_val, d_val)
            except ValueError:
                pass

    # 5. Natural Month-Day (e.g. "24 sep", "24th september", "sep 24", "september 24th")
    month_map = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    # "24 september" or "24th sep" or "24 sept"
    m_day1 = next((m for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b", clean) if m.group(2) in month_map), None)
    if m_day1 and m_day1.group(2) in month_map:
        d_val = int(m_day1.group(1))
        m_val = month_map[m_day1.group(2)]
        y_val = (
            ref.year
            if (m_val > ref.month or (m_val == ref.month and d_val >= ref.day))
            else ref.year + 1
        )
        try:
            return date(y_val, m_val, d_val)
        except ValueError:
            pass

    # "sep 24" or "october 15" or "september 24th"
    m_day2 = next((m for m in re.finditer(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", clean) if m.group(1) in month_map), None)
    if m_day2 and m_day2.group(1) in month_map:
        m_val = month_map[m_day2.group(1)]
        d_val = int(m_day2.group(2))
        y_val = (
            ref.year
            if (m_val > ref.month or (m_val == ref.month and d_val >= ref.day))
            else ref.year + 1
        )
        try:
            return date(y_val, m_val, d_val)
        except ValueError:
            pass

    return None


def parse_date_range(
    text: str,
    reference_date: Optional[date] = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> Optional[Tuple[date, date]]:
    """
    Parses a natural date range from text.
    Handles:
    - '24 Sept - 29 Sept'
    - '24 Sept to 29 Sept'
    - 'from 24 Sept to 29 Sept'
    - '24th September - 29th September'
    - '24/09 to 29/09'
    - '24-09 to 29-09'
    - '24 Sept for 5 days'
    """
    if not text or not str(text).strip():
        return None

    clean = str(text).strip().lower()
    ref = reference_date or get_current_date(tz_name)

    # 1. Pattern: '<start> for <N> days'
    m_for_days = re.match(r"^(?:from\s+)?(.+?)\s+for\s+(\d+)\s*days?$", clean)
    if m_for_days:
        s_date = resolve_relative_date(m_for_days.group(1), reference_date=ref, tz_name=tz_name)
        n_days = int(m_for_days.group(2))
        if s_date and n_days >= 1:
            e_date = s_date + timedelta(days=max(0, n_days - 1))
            return (s_date, e_date)

    # 2. Pattern: '<start> to <end>' or '<start> - <end>' or 'from <start> to <end>'
    range_match = re.match(r"^(?:from\s+)?(.+?)\s+(?:to|until|till|[-–—])\s+(.+)$", clean)
    if range_match:
        part1 = range_match.group(1).strip()
        part2 = range_match.group(2).strip()
        d1 = resolve_relative_date(part1, reference_date=ref, tz_name=tz_name)
        d2 = resolve_relative_date(part2, reference_date=d1 or ref, tz_name=tz_name)
        if d1 and d2:
            if d2 < d1:
                # Year rollover (e.g. 28 Dec to 3 Jan)
                try:
                    d2 = date(d1.year + 1, d2.month, d2.day)
                except ValueError:
                    pass
            if d2 >= d1:
                return (d1, d2)

    return None


def derive_trip_dates(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    number_of_days: Optional[int] = None,
    explicit_nights: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Deterministically derives and synchronizes trip dates, duration, and nights:
    - Rule A: Explicit check-in + check-out: duration_days = (end_date - start_date).days + 1, nights = (end_date - start_date).days
    - Rule B: Duration + check-in: check_out = start_date + (duration_days - 1) days, nights = duration_days - 1
    - Rule C: Check-in only: check_in = start_date, check_out = None
    - Rule D: Duration only: duration_days = number_of_days, nights = number_of_days - 1
    - Rule F: 1-day trip: nights = 0
    """
    derived: Dict[str, Any] = {}

    # Rule A: Both explicit start and end date known
    if start_date is not None and end_date is not None and end_date >= start_date:
        nights = (end_date - start_date).days
        days = nights + 1 if nights > 0 else 1
        derived["trip_start_date"] = start_date
        derived["trip_end_date"] = end_date
        derived["number_of_days"] = days
        derived["number_of_nights"] = explicit_nights if explicit_nights is not None else nights
        return derived

    # Rule B: Duration + start date known
    if start_date is not None and number_of_days is not None and number_of_days >= 1:
        nights = max(0, number_of_days - 1)
        checkout = start_date + timedelta(days=nights)
        derived["trip_start_date"] = start_date
        derived["trip_end_date"] = checkout
        derived["number_of_days"] = number_of_days
        derived["number_of_nights"] = explicit_nights if explicit_nights is not None else nights
        return derived

    # Rule C: Start date only
    if start_date is not None:
        derived["trip_start_date"] = start_date
        derived["trip_end_date"] = None
        return derived

    # Rule D: Duration only
    if number_of_days is not None and number_of_days >= 1:
        nights = max(0, number_of_days - 1)
        derived["number_of_days"] = number_of_days
        derived["number_of_nights"] = explicit_nights if explicit_nights is not None else nights
        return derived

    return derived


def derive_trip_inferences(
    state_or_data: Any,
    explicit_fields: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Step 3 of the intelligence loop (Milestone 3, M3.2):
    Deterministically computes anything that logically follows from raw extracted slots.
    CRITICAL RULE: Derivation should ONLY fill in a field if it's currently empty (or not explicit).
    Explicit information always outranks inferred information.
    1. If duration in days is known but nights is not explicit/known:
       nights = max(0, days - 1)
    2. If nights == 0 and hotel_required is not explicitly set:
       hotel_required = False
    3. If nights > 0 and hotel_required is not explicitly set:
       hotel_required = True
    """
    explicit = set(explicit_fields or [])
    if hasattr(state_or_data, "explicit_fields"):
        for f in state_or_data.explicit_fields:
            explicit.add(f)

    is_dict = isinstance(state_or_data, dict)

    def get_val(key: str) -> Any:
        return state_or_data.get(key) if is_dict else getattr(state_or_data, key, None)

    def set_val(key: str, val: Any) -> None:
        if is_dict:
            state_or_data[key] = val
        else:
            setattr(state_or_data, key, val)

    derived: Dict[str, Any] = {}
    days = get_val("number_of_days")
    nights = get_val("number_of_nights")
    hotel_req = get_val("hotel_required")

    # 1. Derive nights from days if nights not explicitly given
    if days is not None and "number_of_nights" not in explicit:
        if nights is None:
            derived_nights = max(0, int(days) - 1)
            set_val("number_of_nights", derived_nights)
            derived["number_of_nights"] = derived_nights
            nights = derived_nights

    # 2. Derive accommodation semantics (Milestone 3, M3.2 Fix #1)
    acc_req = get_val("accommodation_required")
    if "accommodation_required" not in explicit and acc_req is None:
        if "hotel_required" in explicit and hotel_req is not None:
            acc_req = bool(hotel_req)
        elif nights is not None:
            acc_req = (int(nights) > 0)
        elif days is not None:
            acc_req = (int(days) > 1)
        if acc_req is not None:
            set_val("accommodation_required", acc_req)
            derived["accommodation_required"] = acc_req

    acc_booked = get_val("accommodation_booked")
    if "accommodation_booked" not in explicit and acc_booked is None:
        set_val("accommodation_booked", False)
        derived["accommodation_booked"] = False

    # Purely derived: hotel_search_required = accommodation_required and not accommodation_booked
    is_acc_req = bool(get_val("accommodation_required"))
    is_acc_booked = bool(get_val("accommodation_booked"))
    hotel_search_req = is_acc_req and not is_acc_booked
    set_val("hotel_search_required", hotel_search_req)
    derived["hotel_search_required"] = hotel_search_req

    # Legacy hotel_required mirrors hotel_search_required for backward compatibility
    if "hotel_required" not in explicit:
        set_val("hotel_required", hotel_search_req)
        derived["hotel_required"] = hotel_search_req

    return derived

