# Project Musafir — Agentic Travel Planning Execution Plan

## Phase Goal

Move Project Musafir from a reliable conversational/state foundation into the **main product path**:

```text
User travel intent
        ↓
Travel understanding
        ↓
Hotel discovery
        ↓
User hotel selection
        ↓
Attraction discovery
        ↓
User place selection
        ↓
Food / café discovery
        ↓
Route optimization
        ↓
Realistic itinerary generation
        ↓
Final trip plan
        ↓
User changes something
        ↓
Dynamic replanning
```

The goal of this phase is **not** to add more FastPath/date edge cases unless a real regression appears.

The goal is to connect the travel capabilities that already exist into one coherent agentic planning flow.

---

# 1. Current Foundation

Before this phase, the project already has:

- FastAPI backend
- SerpApi integration
- Google Hotels search
- hotel details
- hotel normalization
- places discovery
- restaurants/cafés discovery
- directions
- route optimization
- itinerary generation
- TripState
- SessionState
- ConversationContext
- ActiveQuestion
- FastPath
- LLM/OpenRouter integration
- shared TripChangeSet mutation
- state-version invalidation
- stale-result protection
- hotel result cards
- hotel selection
- public-response sanitization
- natural date handling
- duration/check-in/check-out derivation
- backend regression coverage

The previous state/context/date milestone is considered the **foundation**, not the next feature to expand.

---

# 2. Product Goal of This Phase

Musafir should stop behaving like:

```text
hotel tool
places tool
restaurant tool
route tool
itinerary tool
```

and start behaving like:

```text
one travel-planning agent
```

The user should not need to understand which backend API is being called.

For example:

```text
User:
I'm going to Kerala for 5 days.
I have ₹5000 total for hotels.
I like nature and food.

Musafir:
Understands the trip
→ finds suitable hotels
→ user selects one
→ discovers suitable attractions around the selected hotel
→ discovers food/cafés around useful locations
→ builds a practical route
→ generates a realistic itinerary
```

The exact sequence can vary depending on user intent.

The agent should remain flexible rather than forcing every user through the same wizard.

---

# 3. Core Architecture for This Phase

Keep the existing separation:

```text
                 USER
                   |
                   v
          Conversation Input
                   |
                   v
          Existing Agent Brain
                   |
                   v
         Structured Travel Intent
                   |
                   v
             TripState
                   |
                   v
        Planning Decision Layer
                   |
       +-----------+-----------+
       |           |           |
       v           v           v
    Hotels      Places       Food
       |           |           |
       +-----------+-----------+
                   |
                   v
          Route Planning Layer
                   |
                   v
         Itinerary Planning Layer
                   |
                   v
           User-facing Plan
```

Important:

- The LLM interprets language.
- Deterministic services perform calculations and constraints.
- SerpApi provides live external travel data.
- TripState is the source of truth.
- The frontend renders structured results.
- The agent decides what needs to happen next.

---

# 4. Phase 1 — Define the Planning State Model

## Goal

Extend the existing TripState so it can represent the complete planning lifecycle.

Do not create a separate disconnected planning state.

Use the existing TripState.

## Required conceptual state

```text
TripState
├── trip basics
│   ├── destination
│   ├── origin
│   ├── dates
│   ├── duration_days
│   ├── hotel_nights
│   ├── adults
│   └── children
│
├── hotel
│   ├── hotel_budget
│   ├── hotel_results
│   ├── selected_hotel
│   └── hotel_details
│
├── interests
│   ├── interests
│   ├── dietary_preferences
│   ├── meal_preferences
│   └── pace
│
├── places
│   ├── discovered_places
│   ├── selected_places
│   └── rejected_places
│
├── food
│   ├── discovered_restaurants
│   ├── discovered_cafes
│   ├── selected_food_stops
│   └── dietary constraints
│
├── route
│   ├── route_stops
│   ├── route_segments
│   ├── travel_mode
│   └── route_summary
│
├── itinerary
│   ├── days
│   ├── scheduled activities
│   ├── meal stops
│   ├── travel blocks
│   └── itinerary summary
│
└── planning metadata
    ├── planning_stage
    ├── last_completed_stage
    ├── state_version
    ├── result set references
    └── timestamps/metrics where applicable
```

Do not blindly add every field. Reuse current fields where they already exist.

---

# 5. Phase 2 — Introduce an Explicit Planning Stage

## Goal

The agent needs to know what part of the travel journey it is currently working on.

Add a deterministic concept such as:

```text
planning_stage
```

Possible values:

```text
DISCOVERY
HOTEL_SELECTION
PLACE_DISCOVERY
PLACE_SELECTION
FOOD_DISCOVERY
ROUTE_PLANNING
ITINERARY_PLANNING
READY
REPLANNING
```

The exact enum names can follow the project's existing naming conventions.

## Important

This is not a rigid wizard.

The stage is a **planning context**, not a mandatory sequence.

Example:

```text
User: Find me restaurants near the Taj Mahal.
```

Musafir should be able to go directly to food discovery without requiring:

```text
hotel
dates
full itinerary
```

---

# 6. Phase 3 — Define the Planning Decision Layer

## Goal

Create a deterministic controller that decides:

```text
What should Musafir do next?
```

It should consume:

```text
TripState
latest user intent
available results
current planning stage
```

and output a structured action.

Conceptually:

```python
PlanningAction(
    type=...,
    reason=...,
    required_data=...,
    tool=...,
)
```

Possible actions:

```text
ASK
SEARCH_HOTELS
SHOW_HOTELS
GET_HOTEL_DETAILS
SEARCH_PLACES
SHOW_PLACES
SEARCH_RESTAURANTS
SHOW_FOOD
OPTIMIZE_ROUTE
GENERATE_ITINERARY
REPLAN
ANSWER
WAIT_FOR_SELECTION
```

The exact structure should integrate with the existing agent/tool framework.

---

# 7. Phase 4 — Define Readiness Rules for Each Planning Action

The deterministic layer should know when an action is actually ready.

## Hotel search readiness

Possible minimum:

```text
destination known
```

Then optional:

```text
dates
travellers
budget
preferences
```

Do not require every field when the request does not need them.

Example:

```text
Find me a hotel in Agra under ₹2000 per night.
```

should be searchable immediately.

---

## Place discovery readiness

Minimum:

```text
destination OR selected hotel OR geographical anchor
```

plus any relevant:

```text
interests
date/time context
distance constraints
```

---

## Restaurant discovery readiness

Minimum:

```text
destination OR anchor location
```

plus:

```text
meal preference
cuisine
dietary preference
price level
distance
```

where available.

---

## Route readiness

Requires enough selected locations to route between.

For example:

```text
hotel
+
selected attraction(s)
+
optional food stops
```

---

## Itinerary readiness

Requires:

```text
dates/duration
selected or sufficiently strong candidate places
route/travel-time information
```

plus any required meal/scheduling constraints.

Do not generate an itinerary before enough information exists.

---

# 8. Phase 5 — Create the Hotel-to-Trip Planning Pipeline

## Goal

Connect hotel discovery to the rest of the journey.

Current capability:

```text
hotel search
```

New behavior:

```text
hotel search
    ↓
user selects hotel
    ↓
selected_hotel becomes planning anchor
    ↓
discover relevant places
    ↓
discover nearby/route-aware food
```

The selected hotel should become an important geographical anchor.

---

# 9. Phase 6 — Hotel Selection as a Planning Event

When the user selects a hotel:

```text
the second one
I like Aloha
I'll stay at this one
that hotel
```

do not treat selection as merely a UI flag.

Generate a structured state mutation:

```text
selected_hotel = X
```

Then trigger the appropriate planning transition.

Example:

```text
planning_stage:
HOTEL_SELECTION
        ↓
selected_hotel present
        ↓
PLACE_DISCOVERY
```

However, if the user asks a different question, do not automatically start place discovery.

Example:

```text
User:
I like the second hotel. How far is it from the airport?
```

The immediate action may be:

```text
ANSWER
```

not:

```text
SEARCH_PLACES
```

---

# 10. Phase 7 — Add Attraction Discovery Around the Selected Hotel

After hotel selection and a request such as:

> “What can I visit nearby?”

search around:

```text
selected_hotel.coordinates
```

Use existing places tooling.

The agent should be able to understand:

```text
near my hotel
nearby
close to the hotel
within 10 km
things around here
```

The deterministic layer converts the selected hotel into the search anchor.

---

# 11. Phase 8 — Add Interest-Aware Attraction Discovery

Use user interests to construct discovery queries.

Examples:

```text
nature
food
history
caves
beaches
adventure
temples
culture
```

The LLM can interpret the user's natural expression into structured interests.

The deterministic layer should then construct bounded search parameters/queries.

Example:

```text
User:
I love caves and nature.
```

Possible structured state:

```text
interests = ["caves", "nature"]
```

Then discovery can use those interests.

Do not hardcode every possible interest.

Use general semantic categories with normalization.

---

# 12. Phase 9 — Structure Place Results for Planning

Each place needs enough information to become a route/itinerary candidate.

Conceptual fields:

```text
id
name
location
latitude
longitude
category
rating
price_level if applicable
opening_hours if available
estimated_visit_duration
source
```

Do not invent fields if unavailable.

When visit duration is not known, use existing deterministic defaults.

Current itinerary defaults include:

```text
attraction = 60 minutes
restaurant = 60 minutes
café = 45 minutes
```

---

# 13. Phase 10 — Add Place Selection

The user should be able to say:

```text
the first one
the second one
the last one
I like the caves
keep this place
remove the waterfall
```

Maintain visible result-set identity exactly as was done for hotels.

Example:

```text
result_set_id = places_01

1 -> Cave A
2 -> Waterfall B
3 -> Nature Park C
```

Then:

```text
the second one
```

must map to:

```text
selected_places += Waterfall B
```

No unnecessary LLM call for a deterministic ordinal.

---

# 14. Phase 11 — Add Rejection/Removal Semantics

The planner needs to understand that users may explicitly reject a place.

Examples:

```text
remove the waterfall
I don't want this one
skip the museum
not interested in this
remove the last place
```

Represent this separately from merely not selecting something.

Possible state:

```text
rejected_places
```

This prevents the agent from immediately recommending the same place again during re-search.

---

# 15. Phase 12 — Build Food Discovery Around the Actual Plan

Food should not just be:

```text
restaurants in destination
```

It should become location-aware.

Support:

```text
near hotel
near attraction
near route segment
near next destination
between two selected locations
```

The first implementation should prioritize:

```text
near selected hotel
near selected attraction
```

Then expand to route-aware discovery.

---

# 16. Phase 13 — Make Food Preferences Actionable

Use existing:

```text
dietary preferences
meal preferences
cuisine
budget
```

Examples:

```text
vegetarian
vegan
breakfast
lunch
dinner
street food
café
cheap
mid-range
```

The agent should convert the natural request into structured search criteria where supported.

The LLM interprets.

The service/search layer executes.

---

# 17. Phase 14 — Define Planning Relationships

This is where Musafir becomes more than a collection of searches.

Support relationships such as:

```text
NEAR
BETWEEN
ALONG_ROUTE
FROM
TO
NEXT_TO
WITHIN_DISTANCE
WITHIN_TRAVEL_TIME
```

Examples:

```text
restaurants near hotel
cafés near attraction
food between attraction A and attraction B
places within 5 km
restaurant within 15 minutes of the next stop
```

Represent relationships structurally where practical.

Do not leave them as free-form text all the way through the system.

---

# 18. Phase 15 — Build a Candidate Planning Set

Before route optimization, create a normalized set of candidates.

Conceptually:

```text
PlanningCandidate
├── location
├── candidate_type
│   ├── hotel
│   ├── attraction
│   ├── restaurant
│   └── cafe
├── source
├── priority/relevance
├── user_selected
├── user_rejected
├── estimated_duration
└── constraints
```

This provides a clean bridge between discovery and route planning.

---

# 19. Phase 16 — Planning Scoring

Do not let the LLM decide all route ordering.

Create deterministic scoring based on available facts.

Potential scoring dimensions:

```text
user interest match
distance/travel cost
opening-time compatibility
meal-window compatibility
user selection
budget compatibility
route efficiency
duplicate/redundant location penalty
```

The exact weights should be configurable.

Do not introduce a giant opaque scoring function initially.

Start with a small interpretable scoring model.

---

# 20. Phase 17 — Connect Selected Places to Route Optimization

The existing route optimizer should receive:

```text
hotel
+
selected places
+
selected food stops where relevant
+
travel mode
+
hard constraints
```

Do not route every discovered search result.

Route:

```text
selected
```

or:

```text
shortlisted
```

stops.

The planner should not automatically add every restaurant returned by SerpApi.

---

# 21. Phase 18 — Add Route-Aware Food Stops

After an attraction sequence is chosen:

```text
Hotel
→ Cave
→ Museum
→ Waterfall
```

identify meal windows and route positions.

Then search for food:

```text
near Cave → Museum segment
```

or:

```text
near Museum
```

depending on the route.

This is the beginning of planning around reality instead of simply listing restaurants.

---

# 22. Phase 19 — Integrate Existing Itinerary Engine

The existing itinerary generator already handles:

- durations
- meal windows
- travel time
- day rollover
- day boundaries

Now feed it the output of the unified planner.

Target flow:

```text
selected hotel
+
selected places
+
selected food stops
+
route segments
+
dates
+
constraints
        ↓
itinerary generator
```

Do not duplicate itinerary scheduling logic elsewhere.

---

# 23. Phase 20 — Validate Itinerary Feasibility

After generating an itinerary, run deterministic validation.

Check:

```text
dates valid
travel time fits
visit durations fit
meal windows respected where possible
no impossible overlapping events
daily travel burden within configured constraints
route segments correspond to actual route data
```

Return structured validation information.

Example:

```text
ItineraryValidation
├── valid
├── warnings[]
└── conflicts[]
```

Do not hide conflicts inside LLM prose.

---

# 24. Phase 21 — User-Facing Planning Summary

Once enough of the trip is built, render a structured summary.

Example:

```text
Kerala
24–28 September
5 days / 4 nights

Hotel
Aloha On The Ganges

Day 1
Hotel → Nature spot → Lunch → Cave

Day 2
Museum → Café → Waterfall

...
```

The exact content should come from structured state.

The LLM may provide explanation text, but the factual plan should come from deterministic state.

---

# 25. Phase 22 — Introduce a Plan Object

Instead of relying only on separate fields, create a normalized final planning representation.

Conceptually:

```python
TripPlan(
    destination,
    dates,
    hotel,
    days,
    route,
    assumptions,
    warnings
)
```

The `TripPlan` becomes the object rendered by the frontend and summarized by the agent.

This prevents the UI from reconstructing a plan from scattered state fields.

---

# 26. Phase 23 — Add Dynamic Replanning

This is the major differentiator.

The agent must treat a user change as a planning event.

Examples:

```text
Make it 7 days.
Remove the waterfall.
Add more nature.
Keep the hotel.
Change to Jaipur.
I'll be driving instead.
The budget is now ₹7000.
I don't want that restaurant.
```

Process:

```text
user change
    ↓
TripChangeSet
    ↓
apply state
    ↓
derive dependent fields
    ↓
invalidate affected plan sections
    ↓
recompute only necessary components
    ↓
new route
    ↓
new itinerary
```

Do not rebuild the entire world for every tiny change.

---

# 27. Phase 24 — Replanning Dependency Graph

Define dependencies explicitly.

Example:

```text
Destination
 ├── Hotels
 ├── Places
 ├── Food
 ├── Route
 └── Itinerary

Selected Hotel
 ├── Nearby Places
 ├── Nearby Food
 ├── Route
 └── Itinerary

Selected Places
 ├── Food
 ├── Route
 └── Itinerary

Travel Mode
 ├── Route
 └── Itinerary

Duration/Dates
 └── Itinerary

Hotel Budget
 └── Hotel selection/results
```

This allows targeted invalidation.

---

# 28. Phase 25 — Example Replanning Scenarios

## Scenario A — Extend duration

Initial:

```text
5 days
24–28 Sept
```

User:

```text
make it 7 days
```

Expected:

```text
7 days
24–30 Sept
6 nights
```

Then:

```text
itinerary invalidated
route schedule potentially invalidated
existing compatible hotel preserved if still valid
```

---

## Scenario B — Remove one place

Initial:

```text
Hotel
→ Cave
→ Restaurant
→ Waterfall
```

User:

```text
remove the waterfall
```

Expected:

```text
Waterfall removed
route recalculated
travel times recalculated
itinerary recalculated
```

No need to rediscover the hotel.

---

## Scenario C — Replace an attraction

User:

```text
Remove the waterfall and add another nature spot.
```

Expected:

```text
reject/remove waterfall
discover replacement candidates
user/agent chooses appropriate candidate
rebuild route
rebuild itinerary
```

---

## Scenario D — Travel mode change

User:

```text
I'll be driving instead.
```

Expected:

```text
travel_mode = driving
route invalidated
itinerary invalidated
hotel/places retained
```

Then route is recalculated.

---

## Scenario E — Destination pivot

User:

```text
Actually I'm going to Jaipur instead.
```

Expected:

```text
destination = Jaipur
```

Clear:

```text
Kerala hotels
Kerala places
Kerala food
Kerala route
Kerala itinerary
```

Preserve compatible generic state:

```text
duration
budget
travel mode
general interests
dietary preferences
```

---

# 29. Phase 26 — Avoid Full Rebuilds

Introduce targeted planning invalidation.

For each state change, determine:

```text
what remains valid?
what becomes stale?
what must be recalculated?
```

Example:

```text
remove one attraction
```

should not trigger:

```text
hotel search again
```

It should primarily trigger:

```text
route
itinerary
possibly route-aware food
```

This reduces:

- latency
- SerpApi usage
- unnecessary LLM calls
- state instability

---

# 30. Phase 27 — Agent Tool-Selection Strategy

The LLM should not freely call arbitrary chains without state checks.

Before executing a tool action:

```text
check planning readiness
check current state
check state_version
check required fields
```

Then execute.

Example:

```text
SEARCH_HOTELS
```

requires the hotel search inputs that the backend actually needs.

Example:

```text
OPTIMIZE_ROUTE
```

requires enough valid route stops.

Example:

```text
GENERATE_ITINERARY
```

requires enough information to produce a meaningful itinerary.

The deterministic controller should reject impossible actions.

---

# 31. Phase 28 — Limit Tool Thrashing

Prevent sequences like:

```text
search hotels
search hotels again
search hotels again
same search with slightly different wording
```

Likewise:

```text
places search
same places search
same places search
```

Maintain request/search fingerprints where appropriate.

If a semantically equivalent search has already succeeded and the relevant state has not changed:

```text
reuse current result
```

unless the user explicitly asks to search again.

---

# 32. Phase 29 — Caching Strategy

Reuse current mode-aware caching.

Add or verify cache keys for discovery contexts.

Examples:

```text
destination
anchor
query/category
budget/filter
date context where relevant
travel mode where relevant
state-version/relevance context
```

Do not cache data so aggressively that a changed user constraint incorrectly reuses stale results.

---

# 33. Phase 30 — Result Ranking

SerpApi returns many candidates.

The agent needs a deterministic shortlist.

For hotels, use the existing normalized hotel information.

For places, prioritize:

```text
interest relevance
rating
distance
availability/opening information where present
user constraints
```

For food:

```text
diet compatibility
meal relevance
price
distance
route relevance
```

Do not call a “best hotel” or “best place” judgment a model fact unless it is clearly defined by your ranking algorithm and the UI communicates what the ordering represents.

---

# 34. Phase 31 — Explain Why a Recommendation Appeared

The LLM should explain recommendations using available structured facts.

Example:

```text
This place fits your nature preference and is close to
the route you're already taking.
```

This explanation should be grounded in:

```text
known interest
distance
route
data
```

Avoid inventing facts.

---

# 35. Phase 32 — Keep User Choice Explicit

Do not silently finalize major selections when the product flow expects user choice.

Use the established philosophy:

```text
Agent recommends
→ User decides
→ Agent adapts
```

For example:

```text
Here are 5 places that fit your nature preference.
```

The user can then say:

```text
take the second and fourth.
```

The planner updates accordingly.

---

# 36. Phase 33 — Multi-Selection Support

Support inputs such as:

```text
take the first and third
keep the caves and museum
remove the second one
```

Represent selection changes atomically.

Example:

```text
TripChangeSet:
selected_places = [...]
rejected_places = [...]
```

Then recompute route and itinerary once.

---

# 37. Phase 34 — Build the "Plan Around Reality" Layer

This should become the core product-specific engine.

Inputs:

```text
user preferences
selected locations
route
travel mode
time windows
meal windows
visit durations
budget constraints
```

Outputs:

```text
feasible route
practical daily schedule
appropriate food stops
warnings
```

The goal is not simply:

```text
high-rated places
```

The goal is:

```text
places that work together in one real trip
```

---

# 38. Phase 35 — Add Planning Warnings

Examples:

```text
The last stop makes Day 2 quite travel-heavy.
```

or:

```text
This restaurant is outside the current route and would add
a significant detour.
```

or:

```text
You currently have more selected attractions than fit comfortably
within the available time.
```

Warnings must be derived from deterministic calculations.

The LLM can phrase them naturally.

---

# 39. Phase 36 — Add Plan Alternatives

Eventually support choices like:

```text
Option A — relaxed
Option B — balanced
Option C — packed
```

Do not implement all of this before the core planner works.

Initial implementation should produce one valid plan.

Later, use the deterministic scoring engine to produce alternatives.

---

# 40. Phase 37 — Frontend Planning Workspace

After backend planning is stable, extend the UI.

The frontend should move from:

```text
chat + hotel cards
```

toward:

```text
chat
+
current trip state
+
hotel cards
+
place cards
+
food cards
+
route
+
itinerary
```

Do not make the interface complex initially.

Suggested layout:

```text
------------------------------------------------
| Chat                         | Current Trip   |
|                              |                |
| User / Agent                 | Destination    |
|                              | Dates          |
|                              | Hotel          |
|                              | Preferences    |
|                              |                |
|                              | Itinerary      |
------------------------------------------------
```

On mobile, these can become tabs/sections.

---

# 41. Phase 38 — Plan as a First-Class UI Object

Once a valid plan exists, provide a structured itinerary view.

Each day should show:

```text
Day 1
08:30 — Breakfast
10:00 — Attraction
11:00 — Travel
12:00 — Lunch
13:00 — Attraction
...
```

Use the deterministic itinerary data directly.

The chat can explain changes, but the plan view should be structured.

---

# 42. Phase 39 — User Actions From the Plan UI

The UI should eventually allow:

```text
Remove
Replace
Move
Add
Select
```

These should produce normal TripChangeSet updates so that:

```text
UI action
```

and:

```text
natural-language action
```

go through the same state engine.

Example:

```text
UI: Remove Waterfall
```

should be semantically equivalent to:

```text
User: Remove the waterfall.
```

---

# 43. Phase 40 — Unified Change Pipeline

Everything must converge:

```text
Natural language
    ↓
FastPath or LLM
    ↓
TripChangeSet

UI action
    ↓
TripChangeSet

TripChangeSet
    ↓
shared mutation engine
    ↓
derived state
    ↓
invalidation
    ↓
planning controller
```

This avoids separate business logic in:

- frontend
- FastPath
- LLM
- tools

---

# 44. Phase 41 — End-to-End Main Journey Test

Add a dedicated integration test that represents the actual product.

Suggested conversation:

```text
User:
I'm going to Kerala for 5 days.
My total hotel budget is ₹5000.
I like nature and food.

Agent:
Understands intent
```

Then:

```text
hotel discovery
```

Then:

```text
User:
I'll take the second hotel.
```

Then:

```text
place discovery
```

Then:

```text
User:
Take the first cave and the third nature spot.
```

Then:

```text
food discovery
```

Then:

```text
User:
Add a vegetarian lunch.
```

Then:

```text
route optimization
```

Then:

```text
itinerary generation
```

Then:

```text
User:
Remove the last place and make the trip one day longer.
```

Expected:

```text
duration updated
dates updated
removed place gone
route recalculated
itinerary recalculated
compatible hotel preserved
budget preserved
```

---

# 45. Phase 42 — Main Journey Test Matrix

Test at least:

## Flow A — hotel → places → food → route → itinerary

```text
Pass if complete planning chain works.
```

## Flow B — place-first

```text
User asks for places before selecting a hotel.
```

System should still function.

## Flow C — food-first

```text
User asks for cafés near a landmark.
```

System should not require a full trip.

## Flow D — route-first

```text
User gives A, B, C and asks for best route.
```

System should skip unnecessary hotel questions.

## Flow E — dynamic change

```text
Remove a stop.
```

Route/itinerary update.

## Flow F — destination pivot

```text
Kashmir → Kerala.
```

Stale results removed.

## Flow G — mode change

```text
walking → driving.
```

Route recalculated.

## Flow H — budget change

```text
₹5000 → ₹7000.
```

Hotel search/selection logic updates where necessary.

---

# 46. Phase 43 — Test Tool Call Counts

For deterministic state-only actions:

```text
LLM = 0 when FastPath can safely handle
SerpApi = 0 when no external data is required
```

For actual discovery:

```text
SerpApi > 0
```

For planning calculations:

```text
LLM should not calculate route math
LLM should not calculate date math
```

Track:

```text
LLM calls
SerpApi calls
tool count
latency
```

---

# 47. Phase 44 — Test State-Version Safety in Full Planning

Scenario:

```text
Start Kerala place search
change destination to Jaipur
old Kerala search returns
```

Expected:

```text
old result rejected
```

Then:

```text
new Jaipur search accepted
```

Do the same for:

- hotels
- places
- restaurants
- route
- itinerary where relevant

---

# 48. Phase 45 — Test Partial Invalidation

Examples:

## Duration change

Should invalidate:

```text
itinerary
possibly route schedule
```

but not necessarily:

```text
selected hotel
```

## Travel mode change

Should invalidate:

```text
route
itinerary timing
```

but retain:

```text
hotel
places
food selections
```

## Attraction removal

Should invalidate:

```text
route
itinerary
route-aware food placement
```

but not:

```text
hotel
trip destination
hotel budget
```

---

# 49. Phase 46 — Build Structured Agent Responses

The public agent result should become structured.

Conceptually:

```python
AgentResponse(
    message,
    action,
    hotels=[],
    places=[],
    restaurants=[],
    route=None,
    itinerary=None,
    selected_items=[],
    warnings=[]
)
```

Do not send huge raw tool payloads to the frontend.

The frontend should receive normalized public objects.

---

# 50. Phase 47 — Keep Raw Tool Data Internal

Every tool result should pass through:

```text
tool output
 ↓
normalization
 ↓
safe public representation
```

Never expose:

```text
property_token
SerpApi metadata
credentials
traceback
internal IDs
raw tool messages
```

unless explicitly needed by an internal developer/debug view.

---

# 51. Phase 48 — Error Handling

The agent should distinguish:

```text
search failed
no results
provider timeout
provider rate limit
invalid request
stale response
```

User-facing messages should be concise.

Example:

```text
I couldn't get fresh hotel results right now.
Your trip details are still saved.
```

The system should not lose state merely because an external tool failed.

---

# 52. Phase 49 — No-Result Handling

If no hotels:

```text
Try a wider budget range
or broader area
```

If no places:

```text
Try broader interest/category
```

If no restaurants:

```text
broaden distance or category
```

The expansion strategy should be controlled and bounded.

Avoid repeatedly issuing the same search.

---

# 53. Phase 50 — Bounded Search Expansion

For insufficient results:

```text
initial search
    ↓
normalize
    ↓
check result count
    ↓
controlled expansion
    ↓
dedupe
    ↓
rank
    ↓
stop
```

Possible expansion dimensions:

- broader radius
- broader category
- broader wording
- nearby locality
- alternative query terms

Do not create uncontrolled loops.

---

# 54. Phase 51 — Build a Real Planning Loop

The main agent loop should increasingly look like:

```text
Understand request
      ↓
Check TripState
      ↓
Determine missing/available information
      ↓
Choose action
      ↓
Execute tool if needed
      ↓
Update state
      ↓
Validate result
      ↓
Determine next planning action
      ↓
Return user-facing result
```

Do not return after every tool call unless the user needs to see a result or selection.

---

# 55. Phase 52 — Agent Should Know When to Stop

The system should know when enough planning has been completed.

Examples:

```text
hotel selected
places selected
food selected
route valid
itinerary valid
```

Then:

```text
planning_stage = READY
```

The agent should not keep searching unnecessarily.

---

# 56. Phase 53 — Final Plan Quality Checks

Before presenting the final plan:

```text
destination correct
dates correct
hotel valid
selected places valid
removed places absent
food choices compatible
route consistent
itinerary times valid
travel mode correct
budget constraints respected where applicable
```

Only after these checks:

```text
READY
```

---

# 57. Phase 54 — Logging / Observability

For development, log a compact planning trace.

Example:

```text
TURN 8

USER:
"Take the first cave and third nature spot."

STATE VERSION:
18

ACTION:
SELECT_PLACES

RESULT:
2 places selected

NEXT ACTION:
SEARCH_RESTAURANTS

TOOL:
Google Maps Search

RESULT COUNT:
7

NEXT ACTION:
OPTIMIZE_ROUTE
```

Also track:

```text
duration
tool latency
SerpApi latency
LLM latency
cache hit/miss
state version
```

Do not expose development logs to normal users.

---

# 58. Phase 55 — Security Boundary

Verify that:

```text
API keys
provider metadata
property tokens
internal state
stack traces
debug messages
```

cannot appear in:

- agent chat
- hotel cards
- place cards
- route view
- itinerary view
- API public response

Keep existing sanitization tests.

Add planning-specific leakage tests.

---

# 59. Phase 56 — Frontend Implementation Order

Only after backend planning flow is stable.

Implement in this order:

```text
1. Place cards
2. Restaurant/café cards
3. Selection states
4. Current trip summary
5. Route summary
6. Itinerary view
7. Replanning feedback
8. Mobile polish
```

Do not build an elaborate map UI first.

---

# 60. Phase 57 — Place Cards

Place cards should show only useful data:

```text
name
category
rating
distance where available
short reason
image where available
selection button
```

The “why” can be based on deterministic ranking signals.

Example:

```text
Matches your nature preference
```

---

# 61. Phase 58 — Food Cards

Food cards should show:

```text
name
cuisine
price level
distance
meal relevance
dietary compatibility where available
```

Do not show raw provider metadata.

---

# 62. Phase 59 — Route View

Initial route UI does not require a full interactive map.

Show:

```text
Hotel
 ↓
Place
 ↓
Restaurant
 ↓
Place
 ↓
Hotel
```

with:

```text
distance
travel time
travel mode
```

This is enough to demonstrate the core route-planning value.

---

# 63. Phase 60 — Itinerary View

Show:

```text
Day 1
08:00 breakfast
10:00 attraction
11:00 travel
12:00 lunch
13:00 attraction
...
```

Include travel blocks.

Distinguish:

```text
travel
visit
meal
free time/warnings
```

Use existing itinerary engine output.

---

# 64. Phase 61 — Replanning UI

When user changes something:

```text
User:
Remove the waterfall.
```

show a lightweight status such as:

```text
Updating your route...
```

Then:

```text
Waterfall removed.
Route and itinerary updated.
```

Do not dump internal state changes into the chat.

---

# 65. Phase 62 — End-to-End Demo Narrative

The product should eventually support a compelling demo flow:

```text
1. User gives a natural travel request.

2. Musafir asks only genuinely useful missing information.

3. Musafir discovers real hotels.

4. User selects a hotel.

5. Musafir discovers places suited to the user's interests.

6. User selects several places.

7. Musafir finds food/cafés that fit the actual route.

8. Musafir optimizes the route.

9. Musafir generates a realistic day-by-day itinerary.

10. User changes a constraint.

11. Musafir dynamically replans.

12. Final plan updates without losing context.
```

This demonstrates the main product idea clearly.

---

# 66. Phase 63 — What NOT to Build Yet

Do not expand scope into:

```text
booking
payments
social features
complex authentication
full Google Maps clone
travel marketplace
large recommendation marketplace
multi-agent architecture
second LLM classifier
```

Also avoid spending major development time on tiny parser edge cases unless tests expose a real failure.

---

# 67. Phase 64 — Suggested Backend File Structure

Use the existing project structure and adapt only where necessary.

Conceptual structure:

```text
backend/
└── app/
    ├── agent/
    │   ├── agent.py
    │   ├── loop.py
    │   ├── semantics.py
    │   ├── fast_path.py
    │   ├── context.py
    │   ├── state_update.py
    │   ├── planning_controller.py
    │   └── planning_models.py
    │
    ├── services/
    │   ├── hotel_service.py
    │   ├── place_service.py
    │   ├── restaurant_service.py
    │   ├── directions_service.py
    │   ├── route_service.py
    │   ├── itinerary_service.py
    │   └── planning_service.py
    │
    └── api/
        └── agent.py
```

Do not create duplicate services where an existing implementation already handles the capability.

---

# 68. Phase 65 — Suggested New Planning Models

Possible models:

```python
PlanningAction
PlanningCandidate
TripPlan
PlanDay
PlanStop
PlanWarning
ItineraryValidation
```

Use existing models when they already represent the required concept.

Avoid duplicate representations of the same information.

---

# 69. Phase 66 — Suggested New Planning Service Responsibilities

A planning service can coordinate existing services without owning their low-level API logic.

Responsibilities:

```text
build candidate set
apply constraints
rank candidates
prepare route inputs
invoke route optimizer
prepare itinerary inputs
validate itinerary
build TripPlan
```

Do not move raw SerpApi logic into the planning service.

---

# 70. Phase 67 — Planning Controller Contract

A useful conceptual contract:

```python
next_action = planning_controller.decide(
    state=trip_state,
    context=conversation_context,
    latest_intent=latest_intent
)
```

It should produce a structured action such as:

```text
SEARCH_HOTELS
SEARCH_PLACES
SEARCH_RESTAURANTS
OPTIMIZE_ROUTE
GENERATE_ITINERARY
ASK
ANSWER
REPLAN
```

The controller should not directly call SerpApi.

It decides what needs to happen.

The existing agent/tool layer executes.

---

# 71. Phase 68 — Deterministic Planning Controller Rules

Example:

```text
if user explicitly asks a question:
    answer if state has enough information

elif hotel search requested:
    search hotels

elif hotel selected and user wants nearby places:
    search places around selected hotel

elif places selected and user wants food:
    search food around relevant anchors

elif enough stops selected and route requested:
    optimize route

elif route + schedule inputs are valid:
    generate itinerary

elif a state change invalidates route/itinerary:
    replan required sections

else:
    ask one useful missing question
```

The exact rules should be based on current application state and intent, not hardcoded to one conversation script.

---

# 72. Phase 69 — LLM Responsibility During Planning

The LLM should mainly produce structured intent/action information such as:

```text
user wants nearby places
user selected second hotel
user wants nature spots
user wants vegetarian lunch
user wants to remove the last attraction
user changed travel mode
```

The deterministic layer then:

```text
updates state
checks constraints
executes tools
calculates routes
builds itinerary
```

This keeps the system reliable.

---

# 73. Phase 70 — Protect Against Hallucinated Actions

The LLM must not claim:

```text
I booked this hotel
```

when booking does not exist.

It must not claim:

```text
This restaurant is definitely open
```

unless supported by current data.

It must not claim:

```text
This is the best hotel
```

without a defined basis.

User-facing statements should be grounded in available structured data.

---

# 74. Phase 71 — Plan Explanation Layer

After deterministic planning produces:

```text
TripPlan
```

the LLM can create a natural summary:

```text
I kept the first day lighter because your hotel is
farther from the other attractions.
```

But the reason must be derived from:

```text
travel distance/time
```

not invented.

---

# 75. Phase 72 — Main Integration Test Suite

Create a dedicated suite:

```text
test_step13_agentic_travel_planning.py
```

Target at least these tests:

```text
1. hotel -> selected hotel -> places
2. places -> selected places -> food
3. food -> route integration
4. route -> itinerary
5. complete planning chain
6. multi-selection
7. place removal
8. place replacement
9. duration change
10. travel-mode change
11. budget change
12. destination pivot
13. stale-result rejection
14. no-result fallback
15. bounded search expansion
16. no duplicate search
17. plan validation
18. public-response sanitization
19. state preservation
20. deterministic tool suppression
```

---

# 76. Phase 73 — Test Complete Conversation

Example:

```text
User:
I'm going to Kerala.

User:
5 days.

User:
₹5000 total for hotels.

User:
24 Sept.

Agent:
Finds hotels.

User:
The second one.

Agent:
Shows hotel details.

User:
I want caves and nature.

Agent:
Finds suitable places.

User:
Keep the first and third.

Agent:
Finds food/cafés around the route.

User:
Make lunch vegetarian.

Agent:
Updates food planning.

User:
Build the route.

Agent:
Optimizes route.

User:
Create my itinerary.

Agent:
Generates itinerary.

User:
Remove the last place and make it 6 days.

Agent:
Replans dates, route and itinerary.
```

All state should remain coherent throughout.

---

# 77. Phase 74 — Final Regression Requirement

After adding the new planning system, rerun all existing suites:

```text
test_step10_1_state.py
test_step10_2_tools.py
test_step10_3_llm.py
test_step10_4_agent_loop.py
test_step10_5_agent_chat.py
test_step12_5_conversation.py
test_step12_6_fast_path.py
test_step12_6_integration.py
test_step12_7_natural_date_context.py
test_step12_hotels.py
test_step9_itinerary.py
```

Then:

```text
test_step13_agentic_travel_planning.py
```

No regression from the new planning flow should be accepted.

---

# 78. Phase 75 — Browser E2E

After backend integration passes, test the actual browser.

## Test sequence

```text
1. Start a new trip.

2. User:
   I'm going to Kerala for 5 days.
   I have ₹5000 total for hotels.
   I like nature and food.

3. Verify:
   state reflects:
   Kerala
   5 days
   budget = ₹5000
   interests = nature + food

4. Verify:
   hotel discovery occurs.

5. User:
   The second one.

6. Verify:
   correct current second hotel selected.

7. User:
   Show me nature places and caves near the hotel.

8. Verify:
   relevant place results appear.

9. User:
   Keep the first and third.

10. Verify:
    two places are selected.

11. User:
    Find a vegetarian lunch.

12. Verify:
    restaurant/café discovery uses relevant location and dietary context.

13. User:
    Build the route.

14. Verify:
    route contains selected hotel/place/food stops appropriately.

15. User:
    Create the itinerary.

16. Verify:
    itinerary appears.

17. User:
    Remove the last place and make the trip one day longer.

18. Verify:
    duration updated
    dates updated
    removed place absent
    route recalculated
    itinerary recalculated
    hotel preserved
    budget preserved
```

---

# 79. Phase 76 — Performance Acceptance

For a structured state update:

```text
5 days
24 Sept
remove the last place
make it 7 days
```

avoid unnecessary SerpApi calls.

For planning:

```text
hotel search
places search
food search
route
itinerary
```

calls should happen only when the current state/action requires them.

Record:

```text
LLM call count
SerpApi call count
tool count
latency
```

---

# 80. Phase 77 — Final Product Acceptance Criteria

The phase is complete when:

```text
[ ] A natural travel request can start planning
[ ] Hotel discovery works from the agent
[ ] Hotel selection becomes persistent trip state
[ ] Places can be discovered around relevant anchors
[ ] Places can be selected naturally
[ ] Places can be rejected/removed
[ ] Food discovery uses relevant location context
[ ] Dietary/meal preferences influence discovery
[ ] Route optimizer consumes selected stops
[ ] Itinerary consumes route/travel data
[ ] Plan validation runs before final presentation
[ ] User changes trigger targeted replanning
[ ] Destination pivots clear stale destination-specific data
[ ] Travel-mode changes rebuild route/itinerary
[ ] Duration changes recalculate date/nights and itinerary
[ ] Budget changes update relevant hotel planning
[ ] Existing selected hotel can be preserved when compatible
[ ] No unnecessary tool thrashing
[ ] No uncontrolled search expansion
[ ] State-version protection still works
[ ] Raw provider data does not leak
[ ] Frontend displays structured hotel/place/food/route/itinerary data
[ ] Full end-to-end planning test passes
[ ] All existing regression suites still pass
[ ] Browser E2E passes
```

---

# 81. Execution Order — Use This Exact Sequence

Do not implement the complete product in one large change.

## Step 1

Define/verify planning state.

```text
TripState
+
planning_stage
+
candidate/result references
```

## Step 2

Build the deterministic planning controller.

```text
TripState
+
latest intent
→
PlanningAction
```

## Step 3

Connect hotel selection → place discovery.

## Step 4

Connect place selection → food discovery.

## Step 5

Add location relationships:

```text
near
between
along route
```

Start with:

```text
near hotel
near attraction
```

## Step 6

Create normalized planning candidates.

## Step 7

Connect selected candidates → route optimizer.

## Step 8

Connect route output → itinerary engine.

## Step 9

Add deterministic itinerary validation.

## Step 10

Build normalized `TripPlan`.

## Step 11

Add targeted dynamic replanning.

## Step 12

Add frontend place/food/route/itinerary views.

## Step 13

Add full integration tests.

## Step 14

Run full regression suite.

## Step 15

Run browser E2E.

## Step 16

Only then polish the demo experience.

---

# 82. First Implementation Milestone

Do not attempt the whole phase immediately.

The first milestone should be:

## "Hotel → Places → Route"

Target:

```text
User gives destination
 ↓
hotel search
 ↓
user selects hotel
 ↓
agent discovers places around hotel
 ↓
user selects 2–4 places
 ↓
agent optimizes route
```

Do not add food and full itinerary until this chain works reliably.

### Acceptance

Conversation:

```text
User:
I'm going to Kerala for 5 days.

User:
₹5000 total.

User:
24 Sept.

Agent:
hotel results

User:
the second one

Agent:
selected hotel

User:
show me nature places and caves near the hotel

Agent:
place results

User:
keep the first and third

User:
build the route
```

Expected:

```text
Hotel
→ selected places
→ valid route
```

with consistent state.

---

# 83. Second Implementation Milestone

## "Route → Food → Itinerary"

Once Milestone 1 works:

```text
existing route
 ↓
food discovery around route
 ↓
user selects meal stops
 ↓
itinerary generation
```

Acceptance:

```text
hotel
+
selected attractions
+
vegetarian lunch
+
route
→
realistic itinerary
```

---

# 84. Third Implementation Milestone

## "Dynamic Replanning"

Once the main planning chain works:

```text
User changes:
duration
destination
place selection
travel mode
budget
food preference
```

and the system selectively recalculates what is actually affected.

This is the milestone that turns the pipeline into the stronger product concept.

---

# 85. Development Rules for This Phase

## Rule 1

Do not replace the current architecture with another architecture.

Build on the verified state/context foundation.

## Rule 2

Do not add a second LLM.

## Rule 3

Do not move deterministic travel calculations into prompts.

## Rule 4

Do not hardcode one conversation path.

## Rule 5

Do not make every search require a full trip.

## Rule 6

Do not search repeatedly when the same valid result already exists.

## Rule 7

Do not rebuild unrelated state after a small change.

## Rule 8

Do not expose raw tool/provider data.

## Rule 9

Do not add UI complexity before backend planning works.

## Rule 10

Every new feature must have tests before it is treated as complete.

---

# 86. Definition of "Good" for the New Agent

A good Musafir turn should answer four questions internally:

```text
1. What does the user want now?

2. What do we already know?

3. What information/tool is actually needed next?

4. What state becomes stale or changes after this action?
```

The user should experience only the resulting useful action.

---

# 87. Target User Experience

The final experience should feel like:

```text
User:
I want a 5-day Kerala trip with nature and food.

Musafir:
Understands the request.

↓
Finds suitable hotels.

User:
Second one.

↓
Selected.

Musafir:
Finds relevant nature/cave attractions.

User:
First and third.

↓
Selected.

Musafir:
Finds food stops that fit the route and preferences.

User:
Build the plan.

↓
Route optimized.

↓
Realistic itinerary generated.

User:
Actually remove the last attraction and add one more day.

↓
Dates change.
Route changes.
Itinerary changes.
Compatible hotel remains.
Budget remains.
No repeated interview.
```

That is the target behavior.

---

# 88. Final Architecture After This Phase

```text
                            USER
                              |
                              v
                    Conversation / UI
                              |
                              v
                    Semantic Interpreter
                       /            \
                  FastPath           LLM
                       \            /
                        \          /
                         v        v
                      TripChangeSet
                             |
                             v
                apply_trip_state_update()
                             |
                             v
                   derive_trip_state()
                             |
                             v
                 dependency invalidation
                             |
                             v
                      state_version
                             |
                             v
                 Planning Controller
                             |
          +------------------+------------------+
          |                  |                  |
          v                  v                  v
       Hotels              Places             Food
          |                  |                  |
          +------------------+------------------+
                             |
                             v
                  Planning Candidate Set
                             |
                             v
                    Constraint / Scoring
                             |
                             v
                     Route Optimizer
                             |
                             v
                   Itinerary Generator
                             |
                             v
                    Plan Validation
                             |
                             v
                         TripPlan
                             |
                             v
                  Public Sanitization
                             |
                             v
                         Frontend
                             |
                             v
                    User changes plan
                             |
                             └───────────────→
                              Replanning loop
```

---

# 89. What This Phase Is Actually Achieving

The individual APIs already exist.

This phase adds the **orchestration intelligence** that connects them.

Before:

```text
hotel API
places API
restaurant API
route API
itinerary API
```

After:

```text
one coherent travel-planning system
```

That is the main product path that should now receive the development effort.

---

# 90. Immediate Next Task

Start with **Milestone 1: Hotel → Places → Route**.

The first coding step should be:

```text
1. Inspect existing TripState/context/result-reference models.
2. Identify the minimum new planning-stage fields required.
3. Define PlanningAction and its contract.
4. Define readiness rules for hotel, place, and route actions.
5. Add the planning-controller tests.
6. Only then implement the controller.
```

Do not start by changing the LLM prompt.

Do not start by building a new frontend page.

The first objective is to make the backend deterministically understand:

```text
hotel selected
+
destination
+
interests
→
place discovery
→
place selection
→
route planning
```

with the existing tools and state system.
