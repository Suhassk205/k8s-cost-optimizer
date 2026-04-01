# inference.py
"""
LLM Inference Pipeline for KubeCost-Gym (OpenAI Client).

Location: ROOT directory (spec §5 requirement).

Environment variables (required):
  - API_BASE_URL: The API endpoint for the LLM (e.g., https://api.openai.com/v1)
  - MODEL_NAME: The model identifier to use for inference (e.g., "gpt-4")
  - HF_TOKEN: Hugging Face API token for Space submission (os.environ.get used)

Runtime requirement: Complete end-to-end in <20 minutes on vcpu=2, memory=8gb.

Reference: PROJECT_SPEC.md §5 Infra Spec, §7 Inference Contract
"""

import os
import json
import sys
from pathlib import Path
from typing import List, Dict, Any
from openai import OpenAI
from env import KubeCostEnv
from graders import ColdStartGrader, EfficientSqueezeGrader, EntropyStormGrader
from models import Observation, Action, ActionType, TrajectoryStep, Trajectory


# ===== ENVIRONMENT VARIABLE VALIDATION =====

def get_env_or_raise(key: str, default: str = None) -> str:
    """
    Get environment variable or raise if missing and no default.
    
    CRITICAL: Uses os.environ.get() as spec-required (not hardcoded strings).
    
    Args:
        key: Environment variable name
        default: Default value if not found (optional)
    
    Returns:
        str: Environment variable value
    
    Raises:
        ValueError: If key not found and no default provided
    """
    value = os.environ.get(key, default)
    if not value:
        raise ValueError(f"Missing required env var: {key}")
    return value


def validate_env():
    """Validate all required environment variables are set.
    
    Required per spec:
      - API_BASE_URL: LLM endpoint
      - MODEL_NAME: Model identifier
      - HF_TOKEN: Hugging Face token (validator provides its own, but must be accessible)
    """
    required = ["API_BASE_URL", "MODEL_NAME", "HF_TOKEN"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")


# ===== INFERENCE PIPELINE =====

class CostOptimizerAgent:
    """
    LLM-based decision agent for cost optimization (OpenAI Client).
    
    Responsibilities:
        - Observe environment state (Observation model)
        - Query LLM for action recommendation (JSON response)
        - Validate response, extract ActionType
        - Execute step, collect trajectory
        - Score trajectory with graders
    """
    
    def __init__(self, model_name: str = None, api_base_url: str = None):
        """
        Initialize OpenAI LLM inference client.
        
        Args:
            model_name: Model ID (from MODEL_NAME env var if not provided)
            api_base_url: API endpoint (from API_BASE_URL env var if not provided)
        
        Spec requirement: Uses OpenAI Client for all LLM calls.
        """
        self.model_name = model_name or os.environ.get("MODEL_NAME")
        self.api_base_url = api_base_url or os.environ.get("API_BASE_URL")
        
        if not self.model_name:
            raise ValueError("MODEL_NAME env var required")
        if not self.api_base_url:
            raise ValueError("API_BASE_URL env var required")
        
        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=os.environ.get("HF_TOKEN"),  # Validator will inject HF_TOKEN
            base_url=self.api_base_url
        )
    
    def decide(self, observation: Observation, task_description: str = "") -> Action:
        """
        Query LLM for action given current observation.
        
        Args:
            observation: Current Observation model
            task_description: Task context for LLM reasoning
        
        Returns:
            Action: Selected action with validation
        
        LLM is prompted to:
            - Analyze cluster state
            - Select action from available ActionType enum
            - Return JSON with action_type field
        
        Error handling:
            - If LLM response invalid: default to MAINTAIN
            - Log failures for debugging
        """
        try:
            # Serialize observation to JSON for context
            obs_json = json.dumps(observation.model_dump(), indent=2)
            
            # Construct prompt
            prompt = f"""Analyze this Kubernetes cluster state and decide on a cost optimization action.

Task: {task_description or "General cost optimization"}

Current Cluster State:
{obs_json}

Available actions:
{', '.join([action.value for action in ActionType])}

Respond with ONLY valid JSON (no markdown or explanation):
{{"action_type": "<one of the above actions>"}}"""
            
            # Query LLM
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are a Kubernetes cost optimization expert. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=100
            )
            
            # Parse response
            response_text = response.choices[0].message.content.strip()
            response_json = json.loads(response_text)
            action_type_str = response_json.get("action_type", "MAINTAIN")
            
            # Validate action type
            action_type = ActionType(action_type_str)
            return Action(action_type=action_type)
            
        except Exception as e:
            print(f"[WARN] LLM decision failed ({e}), defaulting to MAINTAIN", file=sys.stderr)
            return Action(action_type=ActionType.MAINTAIN)
    
    def run_episode(self, env: KubeCostEnv, max_steps: int = 1000, task_name: str = "") -> Trajectory:
        """
        Run one full episode with LLM agent.
        
        Args:
            env: Environment instance (KubeCostEnv)
            max_steps: Max steps before forced termination
            task_name: Task identifier for LLM context
        
        Returns:
            Trajectory: List of trajectory steps for grading
        """
        try:
            obs = env.reset()
            trajectory_steps = []
            
            for step_num in range(max_steps):
                # Get action from LLM
                action = self.decide(obs, task_name)
                
                # Execute step in environment
                obs_next, reward, done, info = env.step(action)
                
                # Create trajectory step
                trajectory_step = TrajectoryStep(
                    observation=obs,
                    action=action.action_type,
                    reward=reward,
                    done=done,
                    info=info
                )
                trajectory_steps.append(trajectory_step)
                
                obs = obs_next
                
                if done:
                    break
            
            return Trajectory(steps=trajectory_steps)
            
        except Exception as e:
            print(f"[ERROR] Episode failed: {e}", file=sys.stderr)
            return Trajectory(steps=[])
    
    def evaluate_task(self, env: KubeCostEnv, task_name: str, grader) -> float:
        """
        Run episode and score with appropriate grader.
        
        Args:
            env: Environment instance
            task_name: Task identifier
            grader: Grader instance
        
        Returns:
            float: Score from grader [0.0, 1.0]
        """
        trajectory = self.run_episode(env, task_name=task_name)
        score = grader.grade(trajectory.steps)
        return score


# ===== MAIN INFERENCE ENTRY POINT =====

def main():
    """
    Main inference entry point.
    
    Spec requirements (PROJECT_SPEC.md §5 Infra Spec):
        - Runs when executed: python inference.py
        - Must complete in <20 minutes on vcpu=2, memory=8gb
        - Reads API_BASE_URL, MODEL_NAME, HF_TOKEN from environment
        - Uses OpenAI Client for all LLM calls
        - Outputs results (trajectories, scores)
    
    Workflow:
        1. Validate environment variables
        2. Initialize agent and environments
        3. Run 3 tasks:
           - cold_start.json with ColdStartGrader
           - efficient_squeeze.json with EfficientSqueezeGrader
           - entropy_storm.json with EntropyStormGrader
        4. Collect trajectories and scores
        5. Verify all scores in [0.0, 1.0] range
        6. Output results to console/file
        7. Exit successfully (code 0) if all scores valid, else exit 1
    """
    
    try:
        # Validate environment
        validate_env()
        print("✓ Environment variables validated")
        print(f"  - API_BASE_URL: {os.environ.get('API_BASE_URL')}")
        print(f"  - MODEL_NAME: {os.environ.get('MODEL_NAME')}")
        print(f"  - HF_TOKEN: {'*' * 4} (hidden)")
        
        # Initialize agent
        model_name = os.environ.get("MODEL_NAME")
        api_base_url = os.environ.get("API_BASE_URL")
        agent = CostOptimizerAgent(model_name=model_name, api_base_url=api_base_url)
        print(f"✓ Agent initialized with model: {model_name}")
        
        # Define tasks
        tasks = [
            {
                "name": "cold_start",
                "trace": "traces/trace_v1_coldstart.json",
                "grader": ColdStartGrader(),
                "description": "Scale cluster from 0→5 replicas without SLA breach"
            },
            {
                "name": "efficient_squeeze",
                "trace": "traces/trace_v1_squeeze.json",
                "grader": EfficientSqueezeGrader(),
                "description": "Maintain <20% steal over 24-hour load cycle"
            },
            {
                "name": "entropy_storm",
                "trace": "traces/trace_v1_entropy.json",
                "grader": EntropyStormGrader(),
                "description": "Proactive REBALANCE_NODE before steal>20%"
            }
        ]
        
        results = {}
        
        # Run inference on each task
        for task in tasks:
            print(f"\n[{task['name'].upper()}]")
            print(f"  Description: {task['description']}")
            
            try:
                env = KubeCostEnv(task["trace"])
                trajectory = agent.run_episode(env, task_name=task["name"])
                score = task["grader"].grade(trajectory.steps)
                
                # Validate score in [0.0, 1.0]
                if not (0.0 <= score <= 1.0):
                    raise ValueError(f"Score {score} outside bounds [0.0, 1.0]")
                
                results[task["name"]] = score
                print(f"  Score: {score:.3f}")
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                results[task["name"]] = 0.0
        
        # Print summary
        print("\n" + "=" * 50)
        print("INFERENCE RESULTS SUMMARY")
        print("=" * 50)
        for task_name, score in results.items():
            status = "✓" if 0.0 <= score <= 1.0 else "✗"
            print(f"  {status} {task_name}: {score:.3f}")
        
        avg_score = sum(results.values()) / len(results) if results else 0.0
        print(f"\nAverage Score: {avg_score:.3f}")
        print("=" * 50)
        
        # Exit with success
        sys.exit(0)
        
    except Exception as e:
        print(f"✗ Inference failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
