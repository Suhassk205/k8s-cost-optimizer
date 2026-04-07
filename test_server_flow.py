import requests
import time

BASE_URL = "http://127.0.0.1:7860"

def test_flow():
    print("Testing flow...")
    try:
        # 1. Reset
        print("POST /reset...")
        r = requests.post(f"{BASE_URL}/reset", json={"task_name": "cold_start"})
        print(f"Status: {r.status_code}")
        print(f"Response: {r.json()}")
        
        # 2. State
        print("GET /state...")
        r = requests.get(f"{BASE_URL}/state")
        print(f"Status: {r.status_code}")
        print(f"Response: {r.json()}")
        
        # 3. Step
        print("POST /step...")
        r = requests.post(f"{BASE_URL}/step", json={"action_type": "MAINTAIN"})
        print(f"Status: {r.status_code}")
        print(f"Response: {r.json()}")
        
        # 4. State
        print("GET /state after step...")
        r = requests.get(f"{BASE_URL}/state")
        print(f"Status: {r.status_code}")
        print(f"Response: {r.json()}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_flow()
