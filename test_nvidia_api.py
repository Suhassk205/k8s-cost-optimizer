"""
Quick test script to verify NVIDIA API connection and response format.
"""

from openai import OpenAI

# Initialize client with NVIDIA credentials
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="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"
)

print("[TEST] Testing NVIDIA API connection...")

try:
    # Simple test prompt
    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Return only this JSON: {\"action_type\": \"MAINTAIN\"}"},
        ],
        temperature=0.3,
        max_tokens=50,
    )

    print("[SUCCESS] API connection successful!")
    print(f"Response type: {type(completion)}")
    print(f"Choices: {completion.choices}")

    if completion.choices and len(completion.choices) > 0:
        print(f"First choice: {completion.choices[0]}")
        print(f"Message: {completion.choices[0].message}")
        print(f"Content: {completion.choices[0].message.content}")
    else:
        print("[ERROR] No choices in response!")

except Exception as e:
    print(f"[ERROR] API call failed: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
