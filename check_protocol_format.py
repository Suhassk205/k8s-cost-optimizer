from app import app
from fastapi.testclient import TestClient

client = TestClient(app)
print("Testing /reset response format...")
r = client.post("/reset", json={"task_name": "cold_start"})
print(f"Status: {r.status_code}")
print(f"Response: {r.json()}")
print(f"Response keys: {list(r.json().keys())}")

if "cpu_usage_pct" in r.json():
    print("✓ Success: Observation fields are at root.")
else:
    print("× Failure: Observation fields are NOT at root.")

print("\nTesting /state response format...")
r = client.get("/state")
print(f"Status: {r.status_code}")
print(f"Response: {r.json()}")
