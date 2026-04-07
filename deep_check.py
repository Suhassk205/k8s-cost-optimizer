"""
Deep validation check for OpenEnv compliance.
Simulates what the automated OpenEnv checker does.
"""
import json
import sys
from pathlib import Path
from typing import Dict, Any, Tuple

print("\n" + "="*80)
print("DEEP OPENENV COMPLIANCE CHECK")
print("="*80)

# ============================================================================
# CHECK 1: FILE EXISTENCE (Critical for Dockerfile)
# ============================================================================
print("\n[CHECK 1] FILE EXISTENCE - Dockerfile requirements")
print("-"*80)

required_files = {
    "Dockerfile": "Docker configuration",
    "inference.py": "Inference implementation (repo root)",
    "app.py": "Main application (repo root)",
    "env.py": "Environment implementation",
    "models.py": "Pydantic models",
    "graders.py": "Grading functions",
    "openenv.yaml": "OpenEnv specification",
    "traces/trace_v1_coldstart.json": "Trace data (cold_start)",
    "traces/trace_v1_squeeze.json": "Trace data (efficient_squeeze)",
    "traces/trace_v1_entropy.json": "Trace data (entropy_storm)",
    "pyproject.toml": "Project configuration",
}

files_ok = True
for filename, description in required_files.items():
    exists = Path(filename).exists()
    status = "OK" if exists else "MISSING"
    symbol = "[OK]" if exists else "[XX]"
    print(f"  {symbol} {filename:40s} - {description}")
    if not exists:
        files_ok = False

# ============================================================================
# CHECK 2: Dockerfile Analysis
# ============================================================================
print("\n[CHECK 2] DOCKERFILE INTEGRITY")
print("-"*80)

dockerfile_path = Path("Dockerfile")
if dockerfile_path.exists():
    content = dockerfile_path.read_text()

    checks = {
        "Base image (python:3.10-slim)": "python:3.10-slim" in content,
        "UV installation": "uv:latest" in content or "/uv" in content,
        "PORT 7860 exposed": "EXPOSE 7860" in content,
        "CMD uses app.py": "app.py" in content and "CMD" in content,
        "Trace file checks": "trace_v1_coldstart.json" in content,
        "inference.py verification": "inference.py" in content,
    }

    dockerfile_ok = True
    for check_name, result in checks.items():
        symbol = "[OK]" if result else "[XX]"
        print(f"  {symbol} {check_name}")
        if not result:
            dockerfile_ok = False
else:
    print("  [XX] Dockerfile not found!")
    dockerfile_ok = False

# ============================================================================
# CHECK 3: Models - Observation Class Fields
# ============================================================================
print("\n[CHECK 3] OBSERVATION MODEL FIELDS")
print("-"*80)

try:
    from models import Observation, NodeSizeClass, ActionType
    import inspect

    # Get field annotations
    fields = Observation.model_fields
    required_fields = {
        'cpu_usage_pct': float,
        'mem_usage_pct': float,
        'p99_latency_ms': float,
        'http_error_rate': float,
        'cpu_steal_pct': float,
        'active_replicas': int,
        'buffer_depth': int,
        'node_size_class': str,
        'current_hourly_cost': float,
        'node_bin_density': list,
        'reward': float,
        'done': bool,
    }

    models_ok = True
    for field_name, field_type in required_fields.items():
        if field_name in fields:
            field_info = fields[field_name]
            has_default = field_info.default is not None or field_info.default_factory is not None
            symbol = "[OK]"
            default_str = f"(default={field_info.default})" if field_info.default is not None else ""
            print(f"  {symbol} {field_name:25s}: {field_type.__name__:10s} {default_str}")
        else:
            print(f"  [XX] {field_name:25s}: MISSING!")
            models_ok = False

except Exception as e:
    print(f"  [XX] Error loading models: {e}")
    models_ok = False

# ============================================================================
# CHECK 4: OpenEnv YAML Compliance
# ============================================================================
print("\n[CHECK 4] OPENENV.YAML COMPLIANCE")
print("-"*80)

try:
    import yaml

    yaml_path = Path("openenv.yaml")
    if yaml_path.exists():
        spec = yaml.safe_load(yaml_path.read_text())

        yaml_checks = {
            "Name field": spec.get("name") in ["kubecost-gym", "kubecost_gym"],
            "Version field": isinstance(spec.get("version"), str),
            "Description field": bool(spec.get("description")),
            "Tasks field exists": "tasks" in spec,
            "Exactly 3 tasks": len(spec.get("tasks", [])) == 3,
            "Task names correct": {t["name"] for t in spec.get("tasks", [])} == {"cold_start", "efficient_squeeze", "entropy_storm"},
        }

        yaml_ok = True
        for check_name, result in yaml_checks.items():
            symbol = "[OK]" if result else "[XX]"
            print(f"  {symbol} {check_name}")
            if not result:
                yaml_ok = False

        print(f"\n  Tasks defined:")
        for task in spec.get("tasks", []):
            diff = task.get("difficulty", "unknown")
            print(f"    - {task['name']:20s} ({diff})")
    else:
        print("  [XX] openenv.yaml not found!")
        yaml_ok = False

except Exception as e:
    print(f"  [XX] Error validating YAML: {e}")
    yaml_ok = False

# ============================================================================
# CHECK 5: REST API Endpoints
# ============================================================================
print("\n[CHECK 5] REST API ENDPOINT FUNCTIONALITY")
print("-"*80)

try:
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)

    endpoints = {
        ("POST", "/reset"): None,
        ("POST", "/step"): {"action_type": "MAINTAIN"},
        ("GET", "/state"): None,
        ("GET", "/health"): None,
    }

    api_ok = True
    for (method, path), data in endpoints.items():
        try:
            if method == "POST":
                response = client.post(path, json=data) if data else client.post(path)
            else:
                response = client.get(path)

            status_ok = response.status_code == 200
            symbol = "[OK]" if status_ok else "[XX]"
            print(f"  {symbol} {method:4s} {path:20s} -> {response.status_code}")

            if not status_ok:
                api_ok = False
                try:
                    error_data = response.json()
                    if "detail" in error_data:
                        print(f"       Error: {error_data['detail']}")
                except:
                    pass
        except Exception as e:
            print(f"  [XX] {method:4s} {path:20s} -> Exception: {str(e)[:50]}")
            api_ok = False

except Exception as e:
    print(f"  [XX] Error testing endpoints: {e}")
    api_ok = False

# ============================================================================
# CHECK 6: OpenEnv Response Format (Critical)
# ============================================================================
print("\n[CHECK 6] OPENENV RESPONSE FORMAT VALIDATION")
print("-"*80)

try:
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.post('/reset')

    if response.status_code != 200:
        print(f"  [XX] Reset endpoint returned {response.status_code}")
        format_ok = False
    else:
        data = response.json()

        required_keys = {
            "observation": dict,
            "task_name": str,
        }

        format_ok = True
        for key, expected_type in required_keys.items():
            if key in data:
                type_match = isinstance(data[key], expected_type)
                symbol = "[OK]" if type_match else "[XX]"
                print(f"  {symbol} Response key '{key}': {type(data[key]).__name__} (expected {expected_type.__name__})")
                if not type_match:
                    format_ok = False
            else:
                print(f"  [XX] Response key '{key}' MISSING!")
                format_ok = False

        # Check optional OpenEnv keys
        for key in ["reward", "done"]:
            if key in data:
                print(f"  [OK] Optional OpenEnv key '{key}': {data[key]}")

        # Check observation fields
        print(f"\n  Observation contains {len(data['observation'])} fields:")
        obs = data['observation']
        critical_obs_fields = ['cpu_usage_pct', 'p99_latency_ms', 'active_replicas',
                               'node_size_class', 'current_hourly_cost']
        for field in critical_obs_fields:
            if field in obs:
                print(f"    [OK] {field}")
            else:
                print(f"    [XX] {field} MISSING")
                format_ok = False

except Exception as e:
    print(f"  [XX] Error validating response format: {e}")
    import traceback
    traceback.print_exc()
    format_ok = False

# ============================================================================
# CHECK 7: pyproject.toml Entry Point
# ============================================================================
print("\n[CHECK 7] PYPROJECT.TOML ENTRY POINT")
print("-"*80)

try:
    toml_path = Path("pyproject.toml")
    if toml_path.exists():
        content = toml_path.read_text()

        entry_checks = {
            "kubecost-gym script defined": "kubecost-gym" in content,
            "Points to app:main": "app:main" in content,
        }

        entry_ok = True
        for check_name, result in entry_checks.items():
            symbol = "[OK]" if result else "[XX]"
            print(f"  {symbol} {check_name}")
            if not result:
                entry_ok = False

        # Verify main() function exists
        try:
            from app import main
            print(f"  [OK] main() function callable in app.py")
        except ImportError:
            print(f"  [XX] main() function not found in app.py")
            entry_ok = False
    else:
        print("  [XX] pyproject.toml not found!")
        entry_ok = False

except Exception as e:
    print(f"  [XX] Error checking pyproject.toml: {e}")
    entry_ok = False

# ============================================================================
# CHECK 8: Inference Implementation
# ============================================================================
print("\n[CHECK 8] INFERENCE IMPLEMENTATION")
print("-"*80)

try:
    inference_path = Path("inference.py")

    if inference_path.exists():
        print(f"  [OK] inference.py exists at repo root")

        try:
            import inference
            print(f"  [OK] inference module imports successfully")

            has_validate = hasattr(inference, 'validate_env')
            has_main = hasattr(inference, 'main')

            print(f"  {'[OK]' if has_validate else '[--]'} validate_env function: {has_validate}")
            print(f"  {'[OK]' if has_main else '[--]'} main function: {has_main}")

            inference_ok = True
        except Exception as e:
            print(f"  [XX] Error importing inference: {e}")
            inference_ok = False
    else:
        print(f"  [XX] inference.py NOT at repo root!")
        inference_ok = False

except Exception as e:
    print(f"  [XX] Error checking inference: {e}")
    inference_ok = False

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*80)
print("DEEP CHECK SUMMARY")
print("="*80)

all_checks = {
    "File Existence": files_ok,
    "Dockerfile Integrity": dockerfile_ok,
    "Observation Model Fields": models_ok,
    "OpenEnv YAML Compliance": yaml_ok,
    "REST API Endpoints": api_ok,
    "Response Format": format_ok,
    "Entry Point Configuration": entry_ok,
    "Inference Implementation": inference_ok,
}

passed = sum(1 for v in all_checks.values() if v)
total = len(all_checks)

for check_name, result in all_checks.items():
    symbol = "[PASS]" if result else "[FAIL]"
    print(f"  {symbol} {check_name}")

print(f"\nResult: {passed}/{total} checks passed")

if passed == total:
    print("\nAll checks PASSED - Ready for OpenEnv deployment!")
    sys.exit(0)
else:
    print(f"\n{total - passed} checks failed - Review above for details")
    sys.exit(1)
