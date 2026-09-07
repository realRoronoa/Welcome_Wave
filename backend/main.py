from fastapi import FastAPI

from ingestion.router import router as ingestion_router
from retrieval.router import router as retrieval_router
from roadmap.router import router as roadmap_router
from verification.router import router as verification_router

app = FastAPI(title="Welcome Wave API")
api_router_prefix = "/api/v1"

app.include_router(ingestion_router, prefix=api_router_prefix)
app.include_router(retrieval_router, prefix=api_router_prefix)
app.include_router(verification_router, prefix=api_router_prefix)
app.include_router(roadmap_router, prefix=api_router_prefix)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
