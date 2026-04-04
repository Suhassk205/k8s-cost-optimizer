# inference.py
"""
LLM Inference Pipeline for KubeCost-Gym (OpenAI Client).

Location:    ROOT directory (spec requirement).
Runtime:     < 20 minutes on vcpu=2, memory=8gb.

Environment variables (all via os.environ.get — never hardcoded):
  API_BASE_URL  : LLM API endpoint
  MODEL_NAME    : Model identifier
  HF_TOKEN      : HuggingFace / API key

Stdout log format (mandatory for automated scoring):
  [START] {"task": "<name>", "model": "<model>", "max_steps": <n>}
  [STEP]  {"task": "<name>", "step": <n>, "action": "<action>",
            "reward": <float>, "done": <bool>, "obs": {...}}
  [END]   {"task": "<name>", "score": <float>, "total_steps": <n>,
            "status": "success"|"error"}
"""

import os
import json
import sys
from typing import List, Dict, Any

from openai import OpenAI

from env import KubeCostEnv
from graders import ColdStartGrader, EfficientSqueezeGrader, EntropyStormGrader
from models import Observation, Action, ActionType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STEPS_PER_TASK = 200

TASKS: List[Dict[str, Any]] = [
    {
        "name":        "cold_start",
        "trace":       "traces/trace_v1_coldstart.json",
        "grader":      ColdStartGrader(),
        "description": "Scale cluster from 0 to 5 replicas without SLA breach (p99 < 300ms).",
        "difficulty":  "easy",
    },
    {
        "name":        "efficient_squeeze",
        "trace":       "traces/trace_v1_squeeze.json",
        "grader":      EfficientSqueezeGrader(),
        "description": "Maintain cpu_steal_pct < 20% across 24-hour sinusoidal load cycle.",
        "difficulty":  "medium",
    },
    {
        "name":        "entropy_storm",
        "trace":       "traces/trace_v1_entropy.json",
        "grader":      EntropyStormGrader(),
        "description": "Issue REBALANCE_NODE before cpu_steal_pct exceeds 20% (proactive).",
        "difficulty":  "hard",
    },
]

# ---------------------------------------------------------------------------
# Structured log helpers — DO NOT alter tag names or field names
# ---------------------------------------------------------------------------

def _emit(tag: str, payload: Dict[str, Any]) -> None:
    print(f"{tag} {json.dumps(payload, default=str)}", flush=True)


def log_start(task_name: str, model: str, max_steps: int) -> None:
    _emit("[START]", {"task": task_name, "model": model, "max_steps": max_steps})


def log_step(task_name: str, step: int, action: str,
             reward: float, done: bool, obs: Observation) -> None:
    obs_dict = obs.model_dump()
    nsc = obs_dict.get("node_size_class")
    obs_dict["node_size_class"] = nsc.value if hasattr(nsc, "value") else str(nsc)
    _emit("[STEP]", {
        "task":   task_name,
        "step":   step,
        "action": action,
        "reward": round(float(reward), 4),
        "done":   bool(done),
        "obs":    obs_dict,
    })


def log_end(task_name: str, score: float, total_steps: int, status: str) -> None:
    _emit("[END]", {
        "task":        task_name,
        "score":       round(float(score), 4),
        "total_steps": total_steps,
        "status":      status,
    })


# ---------------------------------------------------------------------------
# LLM Agent (OpenAI Client — mandatory)
# ---------------------------------------------------------------------------

class CostOptimizerAgent:

    SYSTEM_PROMPT = (
        "You are a Kubernetes cost optimization expert. "
        "Analyse the cluster state and return ONLY a JSON object with one field: "
        "action_type. Choose from the available actions list provided."
    )

    def __init__(self) -> None:
        self.model_name   = os.environ.get("MODEL_NAME", "")
        self.api_base_url = os.environ.get("API_BASE_URL", "")
        self.hf_token     = os.environ.get("HF_TOKEN", "")

        if not self.model_name:
            raise ValueError("MODEL_NAME environment variable is not set.")
        if not self.api_base_url:
            raise ValueError("API_BASE_URL environment variable is not set.")
        if not self.hf_token:
            raise ValueError("HF_TOKEN environment variable is not set.")

        self.client = OpenAI(api_key=self.hf_token, base_url=self.api_base_url)

    def decide(self, obs: Observation, task_description: str = "") -> Action:
        available = ", ".join(a.value for a in ActionType)
        obs_json  = json.dumps(obs.model_dump(), default=str, indent=2)
        prompt    = (
            f"Task: {task_description}\n\n"
            f"Available actions: {available}\n\n"
            f"Current cluster state:\n{obs_json}\n\n"
            'Respond with ONLY valid JSON, e.g. {"action_type": "MAINTAIN"}'
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.3,
                max_tokens=50,
            )
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            return Action(action_type=ActionType(data["action_type"]))
        except Exception as exc:
            print(f"[WARN] LLM decision failed ({exc}), defaulting to MAINTAIN",
                  file=sys.stderr, flush=True)
            return Action(action_type=ActionType.MAINTAIN)

    def run_task(self, task: Dict[str, Any]) -> float:
        task_name   = task["name"]
        description = task["description"]
        grader      = task["grader"]
        trace_path  = task["trace"]

        log_start(task_name, self.model_name, MAX_STEPS_PER_TASK)

        total_steps = 0
        score       = 0.0
        status      = "success"

        try:
            env = KubeCostEnv(trace_path)
            obs = env.reset()

            for step_num in range(1, MAX_STEPS_PER_TASK + 1):
                action = self.decide(obs, description)
                obs, reward, done, _info = env.step(action)
                total_steps = step_num
                log_step(task_name, step_num, action.action_type.value,
                         reward, done, obs)
                if done:
                    break

            score = grader.grade(env.trajectory)
            score = max(0.0, min(1.0, score))

        except Exception as exc:
            print(f"[ERROR] Task '{task_name}' failed: {exc}",
                  file=sys.stderr, flush=True)
            status = "error"
            score  = 0.0

        log_end(task_name, score, total_steps, status)
        return score


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    missing = [k for k in ("API_BASE_URL", "MODEL_NAME", "HF_TOKEN")
               if not os.environ.get(k)]
    if missing:
        print(f"[ERROR] Missing required environment variables: {', '.join(missing)}",
              file=sys.stderr, flush=True)
        sys.exit(1)

    print(f"[INFO] API_BASE_URL : {os.environ.get('API_BASE_URL')}", flush=True)
    print(f"[INFO] MODEL_NAME   : {os.environ.get('MODEL_NAME')}", flush=True)
    print(f"[INFO] HF_TOKEN     : {'*' * 8} (hidden)", flush=True)

    try:
        agent = CostOptimizerAgent()
    except Exception as exc:
        print(f"[ERROR] Agent init failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)

    results: Dict[str, float] = {}
    for task in TASKS:
        results[task["name"]] = agent.run_task(task)

    print("\n" + "=" * 60, flush=True)
    print("INFERENCE RESULTS SUMMARY", flush=True)
    print("=" * 60, flush=True)
    for name, score in results.items():
        flag = "PASS" if 0.0 <= score <= 1.0 else "FAIL"
        print(f"  [{flag}] {name}: {score:.4f}", flush=True)
    avg = sum(results.values()) / len(results) if results else 0.0
    print(f"\n  Average score : {avg:.4f}", flush=True)
    print("=" * 60, flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
