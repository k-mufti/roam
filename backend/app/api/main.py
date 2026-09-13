"""FastAPI application."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import itinerary, meta, places
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

settings = get_settings()

app = FastAPI(
    title="Roam API",
    version="0.1.0",
    description=(
        "Multi-source place aggregation with credibility-weighted scoring and "
        f"itinerary optimization. v1 serves a single city: {settings.target_city}."
    ),
)

# The Vite dev server runs on a different port, so CORS is required locally.
# Scoped to localhost origins rather than "*" — there is no reason for this to
# be callable from anywhere else.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(meta.router, prefix="/api")
app.include_router(places.router, prefix="/api")
app.include_router(itinerary.router, prefix="/api")


@app.get("/")
def root():
    return {
        "name": "Roam API",
        "city": settings.target_city,
        "docs": "/docs",
        "endpoints": [
            "/api/health",
            "/api/facets",
            "/api/places",
            "/api/places/{id}/score",
            "/api/itinerary",
            "/api/meta/provenance",
        ],
    }
