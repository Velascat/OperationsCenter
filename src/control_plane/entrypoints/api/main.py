from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from control_plane.adapters.plane import PlaneClient
from control_plane.config import load_settings

app = FastAPI(title="control-plane")


class ParseRequest(BaseModel):
    config_path: str
    description: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/dry-run/parse")
def dry_run_parse(payload: ParseRequest) -> dict[str, object]:
    settings = load_settings(payload.config_path)
    client = PlaneClient(
        settings.plane.base_url,
        settings.plane_token(),
        settings.plane.workspace_slug,
        settings.plane.project_id,
    )
    try:
        data = client.parse_execution_metadata(payload.description)
        return {"parsed": data}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        client.close()
