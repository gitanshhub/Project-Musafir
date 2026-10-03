"""
Project Musafir — Schema-Driven Multi-Slot Extraction (Milestone 3, M3.2)
Extracts structured trip slots from user input against a fixed schema,
attaching explicit confidence markers (HIGH/LOW) to each extracted slot.
Enforces the discipline: LLM/Extractor only extracts facts; derivation and
readiness are handled downstream by plain deterministic code.
"""

import re
from datetime import date as dt_date
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from app.agent.state import TripState
from app.agent.semantics import (
    parse_currency_amount,
    parse_date_range,
    resolve_relative_date,
)


class SlotConfidence(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class ExtractedTripSlots(BaseModel):
    """
    Structured outcome of multi-slot extraction from a user message.
    """
    destination: Optional[str] = None
    number_of_days: Optional[int] = Field(None, ge=1)
    number_of_nights: Optional[int] = Field(None, ge=0)
    travel_mode: Optional[Literal["driving", "walking", "transit", "bicycling", "two_wheeler"]] = None
    hotel_required: Optional[bool] = None
    accommodation_required: Optional[bool] = None
    accommodation_booked: Optional[bool] = None
    hotel_search_required: Optional[bool] = None
    hotel_budget: Optional[float] = Field(None, gt=0)
    hotel_total_budget: Optional[float] = Field(None, gt=0)
    trip_budget: Optional[float] = Field(None, gt=0)
    dietary_preferences: List[str] = Field(default_factory=list)
    interests: List[str] = Field(default_factory=list)
    trip_start_date: Optional[dt_date] = None
    trip_end_date: Optional[dt_date] = None
    confidence: Dict[str, SlotConfidence] = Field(default_factory=dict)
    explicit_fields: List[str] = Field(default_factory=list)
    raw_message: str = ""

    def has_slots(self) -> bool:
        """Returns True if at least one meaningful slot was extracted."""
        return len(self.explicit_fields) > 0

    def high_confidence_updates(self) -> Dict[str, Any]:
        """
        Returns only the slot values that achieved HIGH confidence.
        Low-confidence extractions are omitted so they do not silently commit as facts.
        """
        updates: Dict[str, Any] = {}
        for field in self.explicit_fields:
            if self.confidence.get(field) == SlotConfidence.HIGH:
                val = getattr(self, field)
                if val is not None:
                    updates[field] = val
        return updates


# Canonical Indian Destinations and Historical Aliases / Alternative Spellings
CANONICAL_DESTINATIONS: Dict[str, str] = {
    "kochi": "Kochi",
    "cochin": "Kochi",
    "kochhi": "Kochi",
    "kerala": "Kerala",
    "jaipur": "Jaipur",
    "udaipur": "Udaipur",
    "jodhpur": "Jodhpur",
    "jaisalmer": "Jaisalmer",
    "goa": "Goa",
    "manali": "Manali",
    "shimla": "Shimla",
    "agra": "Agra",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "chennai": "Chennai",
    "madras": "Chennai",
    "kolkata": "Kolkata",
    "calcutta": "Kolkata",
    "hyderabad": "Hyderabad",
    "pune": "Pune",
    "poona": "Pune",
    "varanasi": "Varanasi",
    "benaras": "Varanasi",
    "banaras": "Varanasi",
    "kashi": "Varanasi",
    "amritsar": "Amritsar",
    "rishikesh": "Rishikesh",
    "haridwar": "Haridwar",
    "munnar": "Munnar",
    "alappuzha": "Alappuzha",
    "alleppey": "Alappuzha",
    "mysuru": "Mysuru",
    "mysore": "Mysuru",
    "ooty": "Ooty",
    "kodaikanal": "Kodaikanal",
    "darjeeling": "Darjeeling",
    "gangtok": "Gangtok",
    "leh": "Leh",
    "ladakh": "Ladakh",
    "kashmir": "Kashmir",
    "srinagar": "Srinagar",
    "puducherry": "Puducherry",
    "pondicherry": "Puducherry",
    "pondy": "Puducherry",
    "vadodara": "Vadodara",
    "baroda": "Vadodara",
    "ahmedabad": "Ahmedabad",
    "hampi": "Hampi",
}

# Known travel modes with rich natural synonyms (including Hinglish)
MODE_PATTERNS = [
    (r"\b(walk|walking|on foot|by walking|by foot|stroll|strolling|paidal|walking pe)\b", "walking"),
    (r"\b(drive|driving|by car|in a car|cab|taxi|self[-\s]?drive|driving myself|road trip|gaadi se|car se)\b", "driving"),
    (r"\b(train|transit|metro|by bus|bus|public transit)\b", "transit"),
    (r"\b(bike|motorcycle|two[-\s]?wheeler|scooter)\b", "two_wheeler"),
    (r"\b(bicycle|bicycling|cycling|cycle)\b", "bicycling"),
]

# Known dietary preferences
DIET_PATTERNS = [
    (r"\b(pure\s+veg(?:etarian)?|vegetarian|veg)\b", "vegetarian"),
    (r"\bvegan\b", "vegan"),
    (r"\bhalal\b", "halal"),
    (r"\bjain\b", "jain"),
    (r"\bgluten[-\s]?free\b", "gluten-free"),
    (r"\bnon[-\s]?veg(?:etarian)?\b", "non-vegetarian"),
]

# Known interests
INTEREST_PATTERNS = [
    (r"\b(forts?|palaces?|monuments?|heritage)\b", "forts"),
    (r"\b(beaches?|coastal)\b", "beaches"),
    (r"\b(museums?|art galleries?)\b", "museums"),
    (r"\b(temples?|churches?|mosques?|spiritual|religious)\b", "temples"),
    (r"\b(nature|wildlife|trekking|hills?|mountains?)\b", "nature"),
    (r"\b(history|historical)\b", "history"),
    (r"\b(culture|cultural)\b", "culture"),
    (r"\b(shopping|markets?|bazaars?)\b", "shopping"),
    (r"\b(architecture|architectural)\b", "architecture"),
    (r"\b(food|street food|local cuisine|dining|culinary)\b", "local food"),
    (r"\b(backwaters?|houseboats?)\b", "backwaters"),
]


def is_negated_match(match_span: tuple, text: str) -> bool:
    """
    Checks if a pattern match at match_span=(start, end) in text
    is preceded by a negation or disinterest expression within the immediate clause.
    Clause boundaries (commas, semicolons, 'but', 'instead', 'I'll') reset negation scope.
    """
    raw_prefix = text[max(0, match_span[0] - 35):match_span[0]].lower()
    clause_parts = re.split(r"[,;.\n]|\b(?:but|instead|we'?ll|i'?ll|will|and\s+i)\b", raw_prefix)
    immediate_prefix = clause_parts[-1]
    negation_patterns = [
        r"\b(?:not|no|don'?t|dont|won'?t|wont|never|isn'?t|isnt|aren'?t|arent|avoid|without|except|excluding)\b",
        r"\b(?:don'?t\s+care\s+about|dont\s+care\s+about|not\s+interested\s+in|hate|dislike)\b",
        r"\b(?:rather\s+than|instead\s+of)\b",
    ]
    return any(re.search(np, immediate_prefix) for np in negation_patterns)


def extract_trip_slots(
    message: str,
    current_state: Optional[TripState] = None,
) -> ExtractedTripSlots:
    """
    Step 1 & 2 of the Turn Intelligence Loop:
    Extracts structured slot values from user input and attaches HIGH/LOW confidence.
    
    Disciplines:
    - Checks against fixed schema (destination, duration, nights, mode, hotel_required, budget, diet, interests).
    - Ambiguous duration (e.g. 'a few days', 'couple of days') is NOT assigned a speculative number.
    - Explicit hotel requests ('day trip but I need a hotel') set hotel_required=True with HIGH confidence.
    - Explicit hotel negations ('2 days in Kochi, no need for a hotel') set hotel_required=False with HIGH confidence.
    - Explicit nights ('5 days, 6 nights') set number_of_nights directly.
    - Normalizes common Indian destination synonyms and aliases ('Cochin' -> 'Kochi', 'Bangalore' -> 'Bengaluru').
    - Handles word-order variations ('Kochi, one day, walking' vs 'Walking trip - Kochi - 1 day').
    """
    slots = ExtractedTripSlots(raw_message=message)
    if not message or not str(message).strip():
        return slots

    raw = str(message).strip()
    # Strip parentheticals often found in WhatsApp forwards/itineraries
    clean = re.sub(r"\(.*?\)", " ", raw).strip()
    # Normalize punctuation and dashes to spaces for flexible tokenization
    clean_spaces = re.sub(r"[-—_]+", " ", clean)
    clean_lower = clean_spaces.lower()

    # -------------------------------------------------------------------------
    # 1. Travel Mode Extraction (Negation & Correction Aware)
    # -------------------------------------------------------------------------
    correction_split = re.search(r"\b(?:actually|no\s+wait|sorry|rather|instead|but\s+(?:this\s+time|now))\b", clean_lower)
    corr_pos = correction_split.start() if correction_split else -1

    mode_candidates = []
    for pat, mode_val in MODE_PATTERNS:
        for m in re.finditer(pat, clean_lower):
            if not is_negated_match(m.span(), clean_lower):
                mode_candidates.append((m.start(), mode_val))

    if mode_candidates:
        # If correction or contrastive marker exists, prioritize candidates appearing after it
        after_corr = [c for c in mode_candidates if c[0] > corr_pos] if corr_pos >= 0 else []
        chosen_mode = after_corr[-1][1] if after_corr else mode_candidates[0][1]
        slots.travel_mode = chosen_mode
        slots.confidence["travel_mode"] = SlotConfidence.HIGH
        slots.explicit_fields.append("travel_mode")

    # -------------------------------------------------------------------------
    # 2. Duration / Days Extraction (Correction Aware)
    # -------------------------------------------------------------------------
    # Explicit negative check: "a few days", "some days", "couple of days"
    ambiguous_duration_pattern = r"\b(a few|some|several|couple(?:\s+of)?)\s+days?\b"
    if re.search(ambiguous_duration_pattern, clean_lower):
        # Explicitly marked LOW confidence and omitted from explicit_fields to prevent guessing
        slots.confidence["number_of_days"] = SlotConfidence.LOW
    else:
        # Mid-sentence self-correction takes highest priority (e.g. "3 days in Jaipur, actually 4 days" or "actually 4")
        dur_corr = re.search(r"\b(?:actually|no\s+wait|make\s+(?:it|that)|sorry|meant)\s+(\d+)\s*(?:days?)?\b", clean_lower)
        if dur_corr:
            slots.number_of_days = int(dur_corr.group(1))
            slots.confidence["number_of_days"] = SlotConfidence.HIGH
            slots.explicit_fields.append("number_of_days")
        elif re.search(r"\b(?:a|1|one)\s+week\b", clean_lower):
            slots.number_of_days = 7
            slots.confidence["number_of_days"] = SlotConfidence.HIGH
            slots.explicit_fields.append("number_of_days")
        elif re.search(r"\b(\d+|two|three)\s+weeks?\b", clean_lower):
            weeks_match = re.search(r"\b(\d+|two|three)\s+weeks?\b", clean_lower)
            w_raw = weeks_match.group(1)
            w_cnt = {"two": 2, "three": 3}.get(w_raw, None) or int(w_raw)
            slots.number_of_days = w_cnt * 7
            slots.confidence["number_of_days"] = SlotConfidence.HIGH
            slots.explicit_fields.append("number_of_days")
        else:
            # 1. Check numeric days FIRST (e.g. "3-day", "3 days", "3 day", "1-day", "1 day")
            day_match = re.search(r"\b(\d+)\s*[- ]*\s*days?\b", clean_lower)
            if day_match:
                days = int(day_match.group(1))
                if days >= 1:
                    slots.number_of_days = days
                    slots.confidence["number_of_days"] = SlotConfidence.HIGH
                    slots.explicit_fields.append("number_of_days")
            else:
                # 2. Check English & Hinglish number words
                word_days = {
                    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                    "ek": 1, "do": 2, "teen": 3, "chaar": 4, "paanch": 5,
                }
                found_word_day = False
                for w, n in word_days.items():
                    if re.search(rf"\b{w}\s*[- ]*\s*(?:days?|din)\b", clean_lower):
                        slots.number_of_days = n
                        slots.confidence["number_of_days"] = SlotConfidence.HIGH
                        slots.explicit_fields.append("number_of_days")
                        found_word_day = True
                        break

                if not found_word_day:
                    # 3. Check day trip / single day / for a day phrasing
                    day_variants = r"\b(?:day\s+trip|single\s+day|for\s+a\s+day|for\s+one\s+day|just\s+for\s+the\s+day|for\s+1\s+day|a\s+day)\b"
                    if re.search(day_variants, clean_lower):
                        slots.number_of_days = 1
                        slots.confidence["number_of_days"] = SlotConfidence.HIGH
                        slots.explicit_fields.append("number_of_days")
                    elif re.search(r"\bweekend\b", clean_lower):
                        slots.number_of_days = 2
                        slots.confidence["number_of_days"] = SlotConfidence.HIGH
                        slots.explicit_fields.append("number_of_days")

    # -------------------------------------------------------------------------
    # 3. Explicit Nights Extraction (e.g. "5 days 6 nights", "0 nights", "overnight")
    # -------------------------------------------------------------------------
    nights_match = re.search(r"\b(\d+)\s*[- ]*\s*nights?\b", clean_lower)
    if nights_match:
        n = int(nights_match.group(1))
        slots.number_of_nights = n
        slots.confidence["number_of_nights"] = SlotConfidence.HIGH
        slots.explicit_fields.append("number_of_nights")
    elif re.search(r"\b(?:no|zero)\s+nights?\b", clean_lower):
        slots.number_of_nights = 0
        slots.confidence["number_of_nights"] = SlotConfidence.HIGH
        slots.explicit_fields.append("number_of_nights")
    elif re.search(r"\b(overnight|one[-\s]night)\b", clean_lower):
        slots.number_of_nights = 1
        slots.confidence["number_of_nights"] = SlotConfidence.HIGH
        slots.explicit_fields.append("number_of_nights")
    else:
        word_nights = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        for w, n in word_nights.items():
            if re.search(rf"\b{w}\s*[- ]*\s*nights?\b", clean_lower):
                slots.number_of_nights = n
                slots.confidence["number_of_nights"] = SlotConfidence.HIGH
                slots.explicit_fields.append("number_of_nights")
                break

    # -------------------------------------------------------------------------
    # 4. Accommodation Semantics & Explicit Overrides (M3.2 Fix #1)
    # -------------------------------------------------------------------------
    hotel_booked_pat = r"\b(hotel(?:'s| is)?\s+(?:already\s+)?booked|booked\s+(?:the\s+|a\s+)?hotel|hotel\s+booked\s+separately|already\s+booked(?:\s+(?:a|the|my)?\s+hotel)?|staying\s+at\s+(?:the\s+)?[A-Za-z]+|sorted\s+(?:my\s+)?(?:hotel|accommodation)|accommodation\s+(?:already\s+)?arranged|have\s+(?:my|our)\s+own\s+stay|booking\s+is\s+done)\b"
    hotel_not_needed_pat = r"\b(no\s+hotel|without(?:\s+a)?\s+hotel|don'?t\s+need(?:\s+a)?\s+hotel|dont\s+need(?:\s+a)?\s+hotel|no\s+need\s+for\s+(?:a\s+)?hotel|no\s+stay|day\s+trip\s+no\s+stay|staying\s+with\s+(?:a\s+)?(?:friend|family|relatives?))\b"
    hotel_needed_pat = r"\b(need(?:\s+a)?\s+hotel|require(?:\s+a)?\s+hotel|with(?:\s+a)?\s+hotel|book(?:\s+a)?\s+hotel|stay(?:\s+at(?:\s+a)?)?\s+hotel|hotel\s+too|hotel\s+though|hotel\s+required|book\s+a\s+hotel\s+too|freshen\s+up(?:\s+at\s+a\s+hotel)?)\b"

    if re.search(hotel_booked_pat, clean_lower):
        # Case 2: Accommodation is required, but already booked/arranged
        slots.accommodation_required = True
        slots.accommodation_booked = True
        slots.hotel_search_required = False
        slots.hotel_required = False
        slots.confidence["accommodation_required"] = SlotConfidence.HIGH
        slots.confidence["accommodation_booked"] = SlotConfidence.HIGH
        slots.confidence["hotel_search_required"] = SlotConfidence.HIGH
        slots.confidence["hotel_required"] = SlotConfidence.HIGH
        slots.explicit_fields.extend(["accommodation_required", "accommodation_booked"])
    elif re.search(hotel_not_needed_pat, clean_lower):
        # Case 1: No accommodation needed for trip at all
        slots.accommodation_required = False
        slots.accommodation_booked = False
        slots.hotel_search_required = False
        slots.hotel_required = False
        slots.confidence["accommodation_required"] = SlotConfidence.HIGH
        slots.confidence["accommodation_booked"] = SlotConfidence.HIGH
        slots.confidence["hotel_search_required"] = SlotConfidence.HIGH
        slots.confidence["hotel_required"] = SlotConfidence.HIGH
        slots.explicit_fields.extend(["accommodation_required", "hotel_required"])
    else:
        m_hotel = re.search(hotel_needed_pat, clean_lower)
        if m_hotel and not is_negated_match(m_hotel.span(), clean_lower):
            # Case 3: Accommodation required and needs search/booking
            slots.accommodation_required = True
            slots.accommodation_booked = False
            slots.hotel_search_required = True
            slots.hotel_required = True
            slots.confidence["accommodation_required"] = SlotConfidence.HIGH
            slots.confidence["accommodation_booked"] = SlotConfidence.HIGH
            slots.confidence["hotel_search_required"] = SlotConfidence.HIGH
            slots.confidence["hotel_required"] = SlotConfidence.HIGH
            slots.explicit_fields.extend(["accommodation_required", "hotel_required"])

    # -------------------------------------------------------------------------
    # 5. Destination Extraction (Robust to Word Order, Aliases & Corrections)
    # -------------------------------------------------------------------------
    dest_extracted = None

    # A. Check for conversational correction first (e.g. "Actually make it Pune, not Goa")
    correction_pat = r"\b(?:actually\s+)?(?:make\s+it|switch\s+to|change\s+to|rather\s+than)\s+([A-Za-z]+)(?:[,\s]+not\s+[A-Za-z]+|\s+instead)?"
    m_corr = re.search(correction_pat, clean, re.IGNORECASE)
    if not m_corr:
        m_corr = re.search(r"\bactually\s+([A-Za-z]+)\s+instead\b", clean, re.IGNORECASE)
    if m_corr:
        cand = m_corr.group(1).strip()
        cand_lower = cand.lower()
        if cand_lower in CANONICAL_DESTINATIONS:
            dest_extracted = CANONICAL_DESTINATIONS[cand_lower]
        elif len(cand) >= 2 and cand_lower not in {"a", "the", "it", "my", "our", "trip", "plan", "make", "walking", "driving", "transit", "cycling", "bike", "car", "cab"}:
            dest_extracted = cand.title()

    # B. Direct canonical dictionary matching if known Indian destination is explicitly present
    # e.g., "Kochi, one day, walking" or "Walking trip — Kochi — 1 day" or "1 day, Kochi, walking"
    # or "I want to walk around Kochi for a day"
    if not dest_extracted:
        found_canonicals = []
        for alias, canonical in CANONICAL_DESTINATIONS.items():
            match = re.search(rf"\b{re.escape(alias)}\b", clean_lower)
            if match and not is_negated_match(match.span(), clean_lower):
                if canonical not in found_canonicals:
                    found_canonicals.append(canonical)
        if len(found_canonicals) == 1:
            dest_extracted = found_canonicals[0]

    # C. Prepositional & relational pattern matching for arbitrary unlisted destinations
    if not dest_extracted:
        dest_patterns = [
            r"(?:trip|travel|tour|vacation|visit|holiday|journey)\s+(?:to|for)\s+([A-Za-z\s]+?)(?:,|$|\s+(?:by|for|with|pure|interested|budget|but|and\b))",
            r"\bto\s+(?!walk|go|travel|visit|see|plan|drive|head|take|explore|stay|book\b)([A-Za-z\s]+?)(?:,|$|\s+(?:by|for|with|pure|interested|budget|but|and\b))",
            r"\b(?:in|visit|explore|around)\s+([A-Za-z\s]+?)(?:,|$|\s+(?:by|for|with|pure|interested|budget|but|and\b))",
            r"\b([A-Za-z]+)\s+for\s+(?:a|\d+|one|two)\s+days?\b",
            r"\b([A-Za-z]+)\s+trip\b",
            r"\b([A-Za-z]+)\s+jaana\s+hai\b",
        ]
        for p in dest_patterns:
            m = re.search(p, clean, re.IGNORECASE)
            if m:
                raw_dest = m.group(1).strip()
                raw_dest = re.sub(
                    r"^(?:visit|explore|see|travel\s+to|go\s+to|head\s+to|reach)\s+",
                    "",
                    raw_dest,
                    flags=re.IGNORECASE,
                ).strip()
                raw_dest = re.sub(
                    r"\b(trip|please|today|tomorrow|car|walking|driving|hotel|budget)\b",
                    "",
                    raw_dest,
                    flags=re.IGNORECASE,
                ).strip()
                if raw_dest and len(raw_dest) >= 2:
                    raw_lower = raw_dest.lower()
                    non_dest_words = {
                        "just", "only", "around", "about", "for", "trip", "maybe", "plan",
                        "planning", "want", "need", "go", "going", "make", "stay", "book",
                        "here", "there", "good", "nice", "looking", "hotel", "walking", "driving",
                        "a", "an", "the", "one", "two", "three", "day", "days", "night", "nights",
                    }
                    if raw_lower not in non_dest_words:
                        dest_extracted = CANONICAL_DESTINATIONS.get(raw_lower, raw_dest.title())
                        break

    if dest_extracted:
        slots.destination = dest_extracted
        slots.confidence["destination"] = SlotConfidence.HIGH
        slots.explicit_fields.append("destination")

    # -------------------------------------------------------------------------
    # 6. Dietary Preferences (Negation Aware)
    # -------------------------------------------------------------------------
    diet_found = []
    for pat, d_val in DIET_PATTERNS:
        for m in re.finditer(pat, clean_lower):
            if not is_negated_match(m.span(), clean_lower):
                if d_val not in diet_found:
                    diet_found.append(d_val)
    if diet_found:
        slots.dietary_preferences = diet_found
        slots.confidence["dietary_preferences"] = SlotConfidence.HIGH
        slots.explicit_fields.append("dietary_preferences")

    # -------------------------------------------------------------------------
    # 7. Interests (Negation Aware & Disambiguated)
    # -------------------------------------------------------------------------
    interests_found = []
    for pat, i_val in INTEREST_PATTERNS:
        for m in re.finditer(pat, clean_lower):
            if is_negated_match(m.span(), clean_lower):
                continue
            # Disambiguate "food" when part of a dietary preference (e.g. "vegetarian food", "veg food")
            if i_val == "local food":
                prefix = clean_lower[max(0, m.start() - 20):m.start()]
                if re.search(r"\b(?:veg|vegetarian|vegan|jain|halal)\s*$", prefix):
                    continue
            # Disambiguate "history" when part of compound noun like "historical forts" or "historical palaces"
            if i_val == "history":
                suffix = clean_lower[m.end():min(len(clean_lower), m.end() + 20)]
                if re.search(r"^\s*(?:forts?|palaces?|monuments?)", suffix):
                    continue
            if i_val not in interests_found:
                interests_found.append(i_val)
    if interests_found:
        slots.interests = interests_found
        slots.confidence["interests"] = SlotConfidence.HIGH
        slots.explicit_fields.append("interests")

    # -------------------------------------------------------------------------
    # 8. Budget Extraction (supports 20k, 1.5L, 20000rs, etc.)
    # -------------------------------------------------------------------------
    for field, amount in extract_budget_slots(clean).items():
        setattr(slots, field, amount)
        slots.confidence[field] = SlotConfidence.HIGH
        slots.explicit_fields.append(field)

    # -------------------------------------------------------------------------
    # 9. Date Extraction
    # -------------------------------------------------------------------------
    date_range = parse_date_range(clean_lower)
    if date_range:
        slots.trip_start_date = date_range[0]
        slots.trip_end_date = date_range[1]
        slots.confidence["trip_start_date"] = SlotConfidence.HIGH
        slots.confidence["trip_end_date"] = SlotConfidence.HIGH
        slots.explicit_fields.extend(["trip_start_date", "trip_end_date"])
    else:
        single_date = resolve_relative_date(clean_lower)
        if single_date:
            slots.trip_start_date = single_date
            slots.confidence["trip_start_date"] = SlotConfidence.HIGH
            slots.explicit_fields.append("trip_start_date")

    return slots


def extract_budget_slots(message: str) -> Dict[str, float]:
    """Scope monetary spans locally, without consuming dates or trip durations."""
    updates: Dict[str, float] = {}
    amount_pattern = r"(?:[₹$]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:k|lakh|lac|l))?\b|\b\d[\d,]*(?:\.\d+)?\s*(?:k|lakh|lac|l|rs|inr|rupees)\b|\b(?:rs\.?|inr)\s*\d[\d,]*(?:\.\d+)?\b)"
    for clause in re.split(r"[;\n]|(?<!\d)\.(?!\d)|\band\b", message.lower()):
        matches = list(re.finditer(amount_pattern, clause))
        if not matches and re.search(r"\b(?:budget|per night|nightly)\b|/night", clause):
            matches = list(re.finditer(r"\b\d[\d,]*(?:\.\d+)?\b", clause))
            if len(matches) != 1:
                continue
        for match in matches:
            amount = parse_currency_amount(match.group())
            if amount is None:
                continue
            if re.search(r"\b(?:per night|a night|nightly)\b|/night", clause):
                field = "hotel_budget"
            elif re.search(r"\b(?:hotel|hotels|accommodation|stay)\b", clause):
                field = "hotel_total_budget"
            else:
                field = "trip_budget"
            updates[field] = amount
    return updates
