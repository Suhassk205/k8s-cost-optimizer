# OpenEnv Reset Error - Root Cause & Fix

## Problem
**Error Message:**
```
Phase 1 — Failed
✗ OpenEnv Reset (POST OK)
Internal Server Error
```

**Root Cause:**
The `openenv-core` library (v0.2.3) expects the `Observation` model to have `reward` and `done` fields as part of its serialization protocol. When the `/reset` endpoint was called, the library's `serialize_observation()` function tried to access these fields:

```python
# From openenv/core/env_server/serialization.py line 163
reward = observation.reward  # AttributeError: 'Observation' object has no attribute 'reward'
done = observation.done
```

## Solution Applied

### 1. Updated `models.py` - Added Missing Fields to Observation

Added two fields to the `Observation` Pydantic model:

```python
class Observation(BaseModel):
    # ... existing fields ...
    
    reward: float = Field(
        default=0.0,
        description="Reward signal for the step (OpenEnv reset response compatibility)"
    )
    done: bool = Field(
        default=False,
        description="Episode termination flag (OpenEnv reset response compatibility)"
    )
```

**Why this works:**
- The `openenv-core` library's `serialize_observation()` function expects these fields
- During `/reset`, the environment hasn't performed any steps yet, so `reward=0.0` and `done=False` are appropriate defaults
- These fields are only included in the HTTP response when using OpenEnv-compatible serialization

### 2. Updated `Dockerfile` - Use app.py Instead of server/app.py

Changed the entry point from the wrapper app to the simpler main app:

```dockerfile
# Before:
CMD ["uv", "run", "python", "server/app.py"]

# After:
CMD ["uv", "run", "python", "app.py"]
```

**Why this helps:**
- `app.py` provides custom, explicit endpoint implementations
- `server/app.py` uses `openenv-core`'s `create_fastapi_app()` which has auto-routing complexity
- The simpler approach avoids additional openenv-core wrapper bugs

### 3. Updated `pyproject.toml` - Consistent Entry Point

```toml
[project.scripts]
kubecost-gym = "app:main"
server = "app:main"
```

### 4. Added main() Function to `app.py`

```python
def main():
    """Entry point for uvicorn runner (for pyproject.toml scripts)."""
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("SERVER_NAME", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")
```

## Verification

All endpoints now work correctly:

```
POST /reset      → 200 OK  ✓
POST /step       → 200 OK  ✓
GET  /state      → 200 OK  ✓
GET  /health     → 200 OK  ✓
```

Response from `/reset`:
```json
{
  "observation": {
    "cpu_usage_pct": 100.0,
    "mem_usage_pct": 100.0,
    "p99_latency_ms": 367.5,
    "http_error_rate": 0.9333,
    "cpu_steal_pct": 1.0,
    "active_replicas": 0,
    "buffer_depth": 133,
    "node_size_class": "S",
    "current_hourly_cost": 10.0,
    "node_bin_density": [0.45, 0.4624, ...]
  },
  "reward": 0.0,
  "done": false,
  "task_name": "cold_start"
}
```

## Impact

- ✓ Fixes "OpenEnv Reset (POST OK) Internal Server Error" 
- ✓ Maintains backwards compatibility (reward/done are optional fields with defaults)
- ✓ Passes all existing validation checks
- ✓ No breaking changes to environment or grader code
- ✓ Simplifies deployment by using the more straightforward app.py

## Files Modified

1. `models.py` - Added reward and done fields to Observation
2. `Dockerfile` - Changed CMD to use app.py
3. `pyproject.toml` - Updated entry point scripts
4. `app.py` - Added main() function for pyproject.toml compatibility
