"""
Lightweight reverse proxy for Lightning.ai API.
Run this on your American server — the Pi sends requests here,
this forwards them to Lightning.ai and returns the response.

Install:  pip install fastapi uvicorn httpx
Run:      python proxy_server.py
          or: uvicorn proxy_server:app --host 0.0.0.0 --port 8000
"""

import os
import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

LIGHTNING_BASE = "https://lightning.ai/api/v1"
API_KEY = os.getenv("LIGHTNING_API_KEY")

app = FastAPI()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request) -> Response:
    body = await request.body()

    # Forward with Lightning.ai auth, strip any auth the Pi sent
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.request(
            method=request.method,
            url=f"{LIGHTNING_BASE}/{path}",
            content=body,
            headers=headers,
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
