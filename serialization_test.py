"""
Test OpenEnv serialization compatibility.
This simulates what the OpenEnv automated checker does.
"""
import json
from pathlib import Path

print("\n" + "="*80)
print("OPENENV SERIALIZATION COMPATIBILITY TEST")
print("="*80)

try:
    from models import Observation, NodeSizeClass
    from env import KubeCostEnv

    print("\n[TEST 1] Direct Observation Serialization")
    print("-"*80)

    # Create a sample observation
    obs = Observation(
        cpu_usage_pct=50.0,
        mem_usage_pct=60.0,
        p99_latency_ms=200.0,
        http_error_rate=0.01,
        cpu_steal_pct=0.05,
        active_replicas=3,
        buffer_depth=10,
        node_size_class=NodeSizeClass.MEDIUM,
        current_hourly_cost=25.0,
        node_bin_density=[0.5]*10,
        reward=0.0,
        done=False
    )

    # Test Pydantic serialization
    obs_dict = obs.model_dump()
    print(f"  Observation.model_dump() successful")
    print(f"  Fields in serialized dict: {len(obs_dict)}")
    print(f"  Has reward: {'reward' in obs_dict}")
    print(f"  Has done: {'done' in obs_dict}")

    # Test JSON serialization
    obs_json = obs.model_dump_json()
    parsed = json.loads(obs_json)
    print(f"  Observation.model_dump_json() successful")
    print(f"  JSON size: {len(obs_json)} bytes")

    test1_ok = True

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    test1_ok = False

# ============================================================================
# TEST 2: OpenEnv Serialization Function
# ============================================================================
print("\n[TEST 2] OpenEnv Serialization Function")
print("-"*80)

try:
    from openenv.core.env_server.serialization import serialize_observation
    from models import Observation, NodeSizeClass

    obs = Observation(
        cpu_usage_pct=50.0,
        mem_usage_pct=60.0,
        p99_latency_ms=200.0,
        http_error_rate=0.01,
        cpu_steal_pct=0.05,
        active_replicas=3,
        buffer_depth=10,
        node_size_class=NodeSizeClass.MEDIUM,
        current_hourly_cost=25.0,
        node_bin_density=[0.5]*10,
        reward=0.0,
        done=False
    )

    # This is what caused the original error
    result = serialize_observation(obs)

    print(f"  serialize_observation() successful")
    print(f"  Result keys: {list(result.keys())}")
    print(f"  Has 'observation': {'observation' in result}")
    print(f"  Has 'reward': {'reward' in result}")
    print(f"  Has 'done': {'done' in result}")
    print(f"  reward value: {result['reward']}")
    print(f"  done value: {result['done']}")

    test2_ok = True

except AttributeError as e:
    print(f"  CRITICAL ERROR - serialize_observation failed: {e}")
    print(f"  This means Observation model is still incompatible!")
    test2_ok = False
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    test2_ok = False

# ============================================================================
# TEST 3: Full Environment Reset Flow
# ============================================================================
print("\n[TEST 3] Full Environment Reset Flow")
print("-"*80)

try:
    from env import KubeCostEnv
    from models import Action, ActionType

    env = KubeCostEnv("traces/trace_v1_coldstart.json")
    obs = env.reset()

    print(f"  KubeCostEnv created successfully")
    print(f"  Environment reset successful")
    print(f"  Observation type: {type(obs).__name__}")
    print(f"  Has reward attribute: {hasattr(obs, 'reward')}")
    print(f"  Has done attribute: {hasattr(obs, 'done')}")
    print(f"  reward value: {obs.reward}")
    print(f"  done value: {obs.done}")

    # Serialize it
    obs_dict = obs.model_dump()
    print(f"  Serialization successful")
    print(f"  Serialized fields: {len(obs_dict)}")

    test3_ok = True

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    test3_ok = False

# ============================================================================
# TEST 4: Environment Step Flow
# ============================================================================
print("\n[TEST 4] Environment Step Flow")
print("-"*80)

try:
    from env import KubeCostEnv
    from models import Action, ActionType

    env = KubeCostEnv("traces/trace_v1_coldstart.json")
    env.reset()

    # Take a step
    obs, reward, done, info = env.step(Action(action_type=ActionType.MAINTAIN))

    print(f"  Environment step successful")
    print(f"  Observation type: {type(obs).__name__}")
    print(f"  reward: {reward} (type: {type(reward).__name__})")
    print(f"  done: {done} (type: {type(done).__name__})")

    # The step returns reward and done separately
    # But the Observation object should also have them
    print(f"  obs.reward: {obs.reward}")
    print(f"  obs.done: {obs.done}")

    test4_ok = True

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    test4_ok = False

# ============================================================================
# TEST 5: HTTP Response Simulation
# ============================================================================
print("\n[TEST 5] HTTP Response Simulation (What AutoChecker Sees)")
print("-"*80)

try:
    from fastapi.testclient import TestClient
    from app import app
    import json

    client = TestClient(app)

    # Simulate the reset call
    response = client.post('/reset')

    if response.status_code != 200:
        print(f"  ERROR: Reset returned status {response.status_code}")
        test5_ok = False
    else:
        data = response.json()

        print(f"  HTTP Status: {response.status_code}")
        print(f"  Response keys: {list(data.keys())}")
        print(f"  Response size: {len(json.dumps(data))} bytes")

        # Check structure
        checks = {
            "Has 'observation'": "observation" in data and isinstance(data['observation'], dict),
            "Has 'task_name'": "task_name" in data and isinstance(data['task_name'], str),
            "Observation has cpu_usage_pct": data.get('observation', {}).get('cpu_usage_pct') is not None,
            "Observation has p99_latency_ms": data.get('observation', {}).get('p99_latency_ms') is not None,
            "Observation has node_size_class": data.get('observation', {}).get('node_size_class') is not None,
        }

        test5_ok = True
        for check, result in checks.items():
            symbol = "[OK]" if result else "[XX]"
            print(f"  {symbol} {check}")
            if not result:
                test5_ok = False

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    test5_ok = False

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*80)
print("SERIALIZATION COMPATIBILITY SUMMARY")
print("="*80)

all_tests = {
    "Direct Observation Serialization": test1_ok,
    "OpenEnv Serialization Function": test2_ok,
    "Full Environment Reset Flow": test3_ok,
    "Environment Step Flow": test4_ok,
    "HTTP Response Simulation": test5_ok,
}

passed = sum(1 for v in all_tests.values() if v)
total = len(all_tests)

for test_name, result in all_tests.items():
    symbol = "[PASS]" if result else "[FAIL]"
    print(f"  {symbol} {test_name}")

print(f"\nResult: {passed}/{total} tests passed")

if passed == total:
    print("\n✓ SERIALIZATION COMPATIBILITY - NO ISSUES!")
    print("   The OpenEnv Reset error has been completely resolved.")
else:
    print(f"\n✗ {total - passed} tests failed")
