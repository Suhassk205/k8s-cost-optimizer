# app.py
# KubeCost-Gym HTTP API Server (OpenEnv Standard)
# Automated REST interface using openenv-core generator

import logging
import os
from typing import Any, Dict

from openenv.core import create_fastapi_app
from env import KubeCostEnv
from models import Action, Observation

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ===== OpenEnv Environment Factory =====

def create_env(**kwargs) -> KubeCostEnv:
    """
    Factory function for KubeCostEnv instances.
    Handles task_name and trace_path overrides from the REST API.
    """
    # Use default trace path
    trace_path = kwargs.get("trace_path", "traces/trace_v1_coldstart.json")
    
    # Map task names to traces (dashboard/validator standard)
    task_name = kwargs.get("task_name")
    if task_name == "efficient_squeeze":
        trace_path = "traces/trace_v1_squeeze.json"
    elif task_name == "entropy_storm":
        trace_path = "traces/trace_v1_entropy.json"
        
    logger.info(f"Creating KubeCostEnv session [task={task_name}, trace={trace_path}]")
    env = KubeCostEnv(trace_path=trace_path)
    return env

# ===== FastAPI App Initialization =====

# Create FastAPI app with automatic REST API generator.
# This ensures that /reset returns a flat Observation object at the root,
# which is essential for validator protocol compliance.
app = create_fastapi_app(
    env=create_env,
    action_cls=Action,
    observation_cls=Observation,
    max_concurrent_envs=None,  # Scale to dashboard load
)

@app.on_event("startup")
async def startup_event():
    """Environment health check on startup."""
    logger.info("KubeCost-Gym server initialization complete.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("SERVER_NAME", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")

def main():
    """Uvicorn entry point."""
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("SERVER_NAME", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")
