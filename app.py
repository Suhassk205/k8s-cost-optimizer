# app.py
"""
OpenEnv HTTP API Server for KubeCost-Gym.

Exposes KubeCostEnv methods as REST endpoints so the OpenEnv automated
checker can interact with the environment over HTTP.

Endpoints:
    POST /reset        -> Reset environment, returns initial Observation JSON
    POST /step         -> Execute action, returns (obs, reward, done, info)
    GET  /state        -> Current EnvState JSON
    GET  /openenv      -> openenv.yaml parsed as JSON
    GET  /health       -> Liveness probe

Port: 7860 (HuggingFace Spaces standard)
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from env import KubeCostEnv
from models import Action, ActionType, Observation, EnvState

app = FastAPI(
    title="KubeCost-Gym",
    description="Kubernetes cost optimization RL environment (OpenEnv standard).",
    version="3.0",
)

TASK_TRACES: Dict[str, str] = {
    "cold_start":        "traces/trace_v1_coldstart.json",
    "efficient_squeeze": "traces/trace_v1_squeeze.json",
    "entropy_storm":     "traces/trace_v1_entropy.json",
}
DEFAULT_TASK = "cold_start"

_env: Optional[KubeCostEnv] = None
_current_task: str = DEFAULT_TASK


def _get_env(task_name: str = DEFAULT_TASK) -> KubeCostEnv:
    global _env, _current_task
    if _env is None or _current_task != task_name:
        trace_path = TASK_TRACES.get(task_name, TASK_TRACES[DEFAULT_TASK])
        _env = KubeCostEnv(trace_path)
        _current_task = task_name
    return _env


class ResetRequest(BaseModel):
    task_name: Optional[str] = DEFAULT_TASK


class StepRequest(BaseModel):
    action_type: str


def _obs_to_dict(obs: Observation) -> Dict[str, Any]:
    d = obs.model_dump()
    nsc = d.get("node_size_class")
    d["node_size_class"] = nsc.value if hasattr(nsc, "value") else str(nsc)
    return d


def _state_to_dict(state: EnvState) -> Dict[str, Any]:
    d = state.model_dump()
    ns = d.get("node_size")
    d["node_size"] = ns.value if hasattr(ns, "value") else str(ns)
    return d


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "env": "kubecost-gym", "version": "3.0"})


@app.get("/openenv")
async def get_openenv() -> JSONResponse:
    yaml_path = Path("openenv.yaml")
    if not yaml_path.exists():
        raise HTTPException(status_code=500, detail="openenv.yaml not found")
    with yaml_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return JSONResponse(data)


@app.post("/reset")
async def reset(request: ResetRequest = None) -> JSONResponse:
    task_name = DEFAULT_TASK
    if request is not None and request.task_name:
        task_name = request.task_name
    if task_name not in TASK_TRACES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown task '{task_name}'. Valid: {list(TASK_TRACES.keys())}"
        )
    try:
        env = _get_env(task_name)
        obs: Observation = env.reset()
        return JSONResponse({"observation": _obs_to_dict(obs), "task_name": task_name})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/step")
async def step(request: StepRequest) -> JSONResponse:
    global _env
    if _env is None:
        raise HTTPException(status_code=400, detail="Call POST /reset first.")
    try:
        action_type = ActionType(request.action_type)
    except ValueError:
        valid = [a.value for a in ActionType]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action_type '{request.action_type}'. Valid: {valid}"
        ) from None
    try:
        action = Action(action_type=action_type)
        obs, reward, done, info = _env.step(action)
        safe_info = {}
        for k, v in info.items():
            try:
                json.dumps(v)
                safe_info[k] = v
            except (TypeError, ValueError):
                safe_info[k] = str(v)
        return JSONResponse({
            "observation": _obs_to_dict(obs),
            "reward": float(reward),
            "done": bool(done),
            "info": safe_info,
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/state")
async def get_state() -> JSONResponse:
    global _env
    if _env is None:
        raise HTTPException(status_code=400, detail="Call POST /reset first.")
    try:
        state: EnvState = _env.state()
        return JSONResponse(_state_to_dict(state))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({
        "name": "kubecost-gym",
        "version": "3.0",
        "endpoints": ["/reset", "/step", "/state", "/openenv", "/health"],
    })


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
