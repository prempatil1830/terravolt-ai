# solar-pv-karnataka

> **Scope: Karnataka, India only.**  
> This project covers site suitability analysis exclusively for the state of
> Karnataka. It is not designed for pan-India or multi-state use in its current
> form.

A spatial decision-support tool for ranking candidate areas for **utility-scale
(≥ 50 MWp) ground-mounted solar PV development** within Karnataka, using an
AHP-based multi-criteria analysis pipeline.

---

## Architecture Overview

```
solar-pv-karnataka/
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variables template
├── backend/                  # FastAPI application
│   └── app/
│       ├── main.py           # FastAPI app entry point, /health endpoint
│       ├── db.py             # MongoDB connection + 2dsphere index setup
│       └── geoprocessing/    # Standalone spatial computation module
│           └── processor.py  # Geopandas / rasterio / shapely pipelines
└── frontend/                 # React + Leaflet map application
    ├── src/
    │   ├── App.jsx           # Leaflet map centred on Karnataka
    │   └── main.jsx
    └── package.json
```

**Key architectural decision:** All heavy geospatial computation (overlays,
raster analysis, AHP scoring) runs in the standalone `geoprocessing/` module
— completely decoupled from the API layer. MongoDB stores only the final ranked
candidate sites as GeoJSON documents; it does not hold raw rasters or
intermediate results.

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | ≥ 3.11 |
| Node.js | ≥ 18 |
| MongoDB | ≥ 6.0 (with `mongod` running locally or Atlas URI) |

---

## Backend Setup

```bash
# Create and activate a virtual environment in the root directory
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment variables
cp .env.example .env
# Edit MONGODB_URI in .env to point to your MongoDB instance

# Change to the backend directory to run the server
cd backend
uvicorn app.main:app --reload --port 8000
```

Health check:
```
GET http://localhost:8000/health
```

---

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — you will see a blank Leaflet map centred on
Karnataka (approx bounding box 11.5–18.5 °N, 74–78.5 °E).

---

## Documentation

- [`assumptions.md`](./assumptions.md) — Data sources, plant-size assumptions,
  AHP framing, and architectural decisions.

---

## Current Phase

**Phase 0 — Scaffold only.**  
No features are implemented. No external data is fetched. No agents are
active. This phase establishes the project skeleton and documents all
upfront assumptions before analysis begins.

---

## Licence

Proprietary. Not for redistribution.
