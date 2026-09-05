"""Root endpoint."""

from fastapi import APIRouter, Request

router = APIRouter(tags=["root"])


@router.get("/")
async def root(request: Request) -> dict[str, str]:
    """Welcome endpoint with API information."""
    return {
        "message": "Welcome to Matik API",
        "description": request.app.description,
        "version": request.app.version,
    }
