# Roam

**A travel planner that behaves like a good travel agent rather than a search engine.**

Roam aggregates places for a city from four independent sources, deduplicates them into single
records, ranks them with a source-credibility weighted score that can explain itself, and builds
an optimised day-by-day itinerary with a real map route.

The thesis: the interesting problem is not *finding* places. It is **fitting them to a person,
then fitting them to a day** — and being able to show your working on both.

---

## Where this actually is

Honest state, because a README that oversells is worse than no README.

**Built and running** — see [`docs/ENGINEERING.md`](docs/ENGINEERING.md) for the full technical
account:

| Area | State |
|---|---|
| Ingestion | Four sources — Google Places, Yelp Fusion, Reddit, Wikivoyage. Live where credentials exist, recorded fixtures otherwise, through the same parser either way. |
| Entity resolution | Deduplication and merge across sources into one `Place` record, in Postgres + PostGIS. |
| Scoring | Source-credibility weighted composite, with a stored breakdown behind every number. |
| Tagging | Negation-aware lexicon, structural signals, VADER sentiment, TF-IDF keywords. |
| Optimizer | Candidate selection → KMeans day clustering → routing → schedule, with opening hours and travel time as constraints. |
| API | FastAPI, with `/api/meta/provenance` reporting each source's last run from the audit table. |
| Web | React + Leaflet in `frontend/`. A second, design-led UI prototype lives in `frontend-next/`. |
| Tests | ~180. |

**Not built.** One city (Madrid — `city` is a real column and query parameter throughout, but
there is no city selector). No accounts, no saved trips, no sharing. Dwell times are category
defaults. Day assignment happens before scheduling rather than jointly. No mobile app.

---

## The problem

Planning a trip well takes about fifteen hours and produces a worse result than a good travel
agent produces in twenty minutes. Not for lack of information — the information is shaped wrongly
for the decision.

- **Reviews are popularity-weighted, not fit-weighted.** A 4.7 from 12,000 people tells you
  almost nothing about whether *you* will like it. Those people wanted something else.
- **Averaging destroys the signal.** A restaurant that is wonderful for two and miserable for six
  averages to "fine". The interesting information is in the variance and the prose, not the mean.
- **Ranked lists don't compose into a day.** Forty "best of" results scattered across a city tell
  you nothing about which three belong together on a Tuesday.
- **Geography is invisible until it hurts.** You book the hotel, then find everything you wanted
  is forty minutes the other way.
- **Nothing explains itself.** You are asked to trust a ranking you cannot inspect, from a system
  paid to sell you something.

---

## What makes it different

These are the load-bearing commitments. They constrain what gets built, not just how it looks.

**Source credibility is weighted, not averaged.** A considered Reddit comment from someone who
lives in the city and a drive-by five-star rating are not equal evidence, and the scoring model
does not pretend otherwise. Four mechanisms each fix a different defect of naive averaging.

**Every score explains itself.** Any recommendation can answer *why*, in plain language, citing
its sources — which threads, which signals, which distance. The explanation is a product
requirement and a debugging tool at once. **If the system cannot explain a recommendation, it
should not make one.**

**Negation is handled, because it is the whole problem.** "Not worth the queue" and "worth the
queue" differ by one token and invert the meaning. Most sentiment pipelines get this wrong on
exactly the prose that carries the most signal.

**Geography is a first-class input.** Days are built from places that genuinely belong together,
clustered by real travel time, sequenced against opening hours.

**Restraint is a feature.** The product should resist overfilling a trip. Most planners are
incentivised to maximise bookings; the defaults and the copy here deliberately push the other way.
An empty day reads *"Left open — and that is often the best day of the trip."*

**Voice and surface are product.** A boutique travel agency, not a SaaS dashboard. Cream ground,
deep plum accent, olive secondary, espresso text; a refined serif for headings; hairline rules
over drop shadows. The copy is dry, confident and unhurried, and is allowed to have opinions.

---

## Where it's going

Ordered by dependency, not calendar.

### 1. Close the loop between what you tell it and what it ranks

**This is the most valuable unbuilt thing, and it is nearly in reach.**

The scoring engine ranks places by credibility-weighted quality. The new UI in `frontend-next/`
asks a traveller who they are — trip type, group size, budget tier, pace, interests like *foodie*
/ *museums* / *nightlife*. **Those answers currently go nowhere near the scorer.**

Wiring them together is what turns a very good aggregator into the product described at the top
of this file: a "family, budget, gentle pace" trip and a "couple, luxury, packed" trip should
surface substantially different cities, not the same ranking reordered. The tagging pipeline
already extracts most of the fit signals this needs — *quiet*, *loud*, *for groups*, *touristy*,
*slow* — they simply aren't consumed as ranking inputs yet.

### 2. One frontend

There are currently two. `frontend/` is functional and wired to the API; `frontend-next/` is a
design-led prototype running on hand-written fixtures with no backend at all. The end state is
one application: the second one's flow, aesthetic and interaction model, talking to the real API.

Concretely, `frontend-next/` needs its fixtures replaced with `/api/places` and `/api/itinerary`,
and it needs a "why this?" surface — the score breakdown already exists server-side and the
prototype has nowhere to show it.

### 3. Hotels as the anchor

The prototype treats hotel choice as the first real decision, because the hotel decides the trip.
The backend has no hotel concept at all. Adding lodging as an entity — ingested, resolved and
scored like anywhere else — and then scoring everything *relative to where you are sleeping* is a
structural improvement, not a feature.

### 4. Curated packages, for real

The prototype shows hand-written packages. Real ones should be generated — run the optimizer
across archetypes ("three days, moderate, food-led") and keep the outputs worth keeping — or
authored by someone who has actually walked the city, and marked as such. The distinction should
be visible to the traveller.

### 5. Accounts, saved trips, collaboration

Save a trip, revisit it, plan several. Share an itinerary by link. Plan one with the people you
are travelling with — which raises group preference reconciliation, deliberately out of scope
until there are accounts to attach preferences to.

### 6. More cities, deliberately

Madrid first. The architecture already supports more (`CityConfig` plus an ingestion run), but
expansion should be city-by-city and researched, not a scraped long tail. **A city ships when it
is genuinely good, not when the pipeline returns rows for it.** The prototype's onboarding shows
Italy, Japan and Argentina as "coming soon" for exactly this reason.

### 7. Travelling with it

Responsive web carries the planning use case. Travelling *with* the itinerary — offline, in a
pocket, one-handed on a street corner — is a different surface and probably a native one. Calendar
and offline export come first and may be most of the value.

### Further out

Weather-aware rerouting. Live crowd data. Per-place dwell times learned rather than defaulted.
Joint cross-day optimisation instead of cluster-then-schedule. Reviewer trust graphs.

---

## Running it

```bash
make setup    # venv + npm install (needs python3.12, node, docker)
make seed     # Postgres+PostGIS, migrate, ingest, score, tag
make dev      # API on :8000, web on :5173
```

Then <http://localhost:5173>, API docs at <http://localhost:8000/docs>.

**No API keys required** — every source falls back to checked-in fixtures, and two of the four run
live with no credentials.

The design prototype runs standalone, with no backend or database:

```bash
cd frontend-next
npm install
npm run dev     # :5174, so it can run alongside `make dev`
```

---

## Layout

```
backend/
  app/
    ingestion/   base.py (contract) · google_places · yelp · reddit · scraper
                 resolver.py (dedup) · mentions.py · normalize.py · runner.py
    models/      place.py (SQLAlchemy) · hours.py · enums.py
    scoring/     model.py (pure, no ORM) · config.py · explain.py
    tagging/     lexicon.py (negation) · structural.py · keywords.py (TF-IDF)
    optimizer/   travel.py · cluster.py · route.py · schedule.py
    api/         FastAPI routes
  alembic/       migrations
  tests/
frontend/        React + Leaflet, wired to the API
frontend-next/   Design-led UI prototype, fixtures only, no backend
docs/
  ENGINEERING.md Full technical account — schema, resolution, scoring, optimizer, CLI, API
```

---

## Principles

1. **Explain or don't recommend.** A recommendation whose reasoning cannot be surfaced is a
   liability, not a feature.
2. **Weight the evidence.** Never average what should be weighted.
3. **The hotel decides the trip.** Geography before everything else.
4. **One question at a time.** Never a form where a sequence will do.
5. **Resist the fill.** Defaults err toward fewer, better stops.
6. **Documents, not dashboards.** The output is something a person would print and carry.
7. **State limitations plainly.** In the README, in the UI, in the provenance endpoint.

---

## License

Not yet chosen.
