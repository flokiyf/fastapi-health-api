from typing import Literal

from fastapi import FastAPI, status
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


app = FastAPI(
    title="FastAPI Health API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check API health",
)
async def health() -> HealthResponse:
    return HealthResponse()
