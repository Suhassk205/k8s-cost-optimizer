# env.py
"""
KubeCost-Gym Environment (OpenEnv Interface).

Phase 3 Implementation: Reward Specification and Physics Logic.
All methods fully implemented — no stub (pass) bodies remain.

Reference: PROJECT_SPEC.md §2 OpenEnv Interface, §3 Reward Spec, §4 Environment Interface
"""

import json
from pathlib import Path
from typing import Tuple, Dict, Any

from models import (
    Observation, Action, EnvState,
    ActionType, NodeSizeClass, TrajectoryStep,
    TraceData, TraceStep,
)

# ---------------------------------------------------------------------------
# Node-size ordering for UPGRADE_NODE logic (irreversible for 1 step).
# ---------------------------------------------------------------------------
_NODE_TIER = {
    NodeSizeClass.SMALL: 0,
    NodeSizeClass.MEDIUM: 1,
    NodeSizeClass.LARGE: 2,
}
_NODE_FROM_TIER = {v: k for k, v in _NODE_TIER.items()}

# Budget used in cost penalty: cost fraction = current_hourly_cost / BUDGET
_HOURLY_BUDGET = 100.0

# Reward bounds (spec §3.3)
_R_MIN = -20.0
_R_MAX = 10.5

# Replica hard bounds
_REPLICAS_MIN = 0
_REPLICAS_MAX = 200


# ---------------------------------------------------------------------------
# Trace loading: Load and validate deterministic trace JSON (Fix #20)
# ---------------------------------------------------------------------------

def load_trace(trace_path: str | Path) -> TraceData:
    """
    Load and validate deterministic trace JSON using Pydantic.

    Args:
        trace_path: Path to JSON file (str or Path).

    Returns:
        TraceData: Validated trace as Pydantic model.

    Raises:
        FileNotFoundError: If trace_path does not exist.
        ValueError: If JSON schema is invalid (caught by Pydantic).
    """
    trace_path = Path(trace_path) if isinstance(trace_path, str) else trace_path
    
    if not trace_path.exists():
        raise FileNotFoundError(
            f"Trace file not found: {trace_path}. "
            f"Expected one of: traces/trace_v1_coldstart.json, "
            f"traces/trace_v1_squeeze.json, traces/trace_v1_entropy.json"
        )

    with trace_path.open("r", encoding="utf-8") as fh:
        data: dict = json.load(fh)

    # Pydantic validates entire structure recursively (replaces 60+ lines of manual checks)
    return TraceData(**data)


# ---------------------------------------------------------------------------
# Reward computation (Fix #21)
# ---------------------------------------------------------------------------

def compute_reward(observation: Observation, previous_steal_pct: float) -> float:
    """
    Calculate episode reward from observation and previous state.

    Implements formula:
        R = (10.0 × Uptime)
          − (5.0 × Cost/Budget)
          − RampPenalty(p99)
          − SLABreach(p99)
          + ProactiveBonus

    Args:
        observation: Current state observation.
        previous_steal_pct: CPU steal from previous step (for proactive bonus).

    Returns:
        float: Reward clamped to [-20.0, +10.5].
    """
    p99 = observation.p99_latency_ms
    cost_fraction = observation.current_hourly_cost / _HOURLY_BUDGET

    # Uptime component
    uptime = 1.0 if p99 < 300.0 else 0.0
    uptime_reward = 10.0 * uptime

    # Cost penalty (capped at 5.0)
    cost_penalty = min(5.0, 5.0 * cost_fraction)

    # Ramp penalty (dense signal in warning zone [200, 300))
    ramp_penalty = 0.0
    if 200.0 <= p99 < 300.0:
        ramp_penalty = ((p99 - 200.0) / 100.0) * 5.0

    # SLA breach hard penalty
    sla_breach_penalty = 20.0 if p99 >= 300.0 else 0.0

    # Proactive bonus (steal dropping + healthy p99)
    proactive_bonus = 0.0
    steal_dropped = observation.cpu_steal_pct < previous_steal_pct
    if steal_dropped and p99 < 300.0:
        proactive_bonus = 0.5

    # Sum and clamp
    raw_reward = (
        uptime_reward
        - cost_penalty
        - ramp_penalty
        - sla_breach_penalty
        + proactive_bonus
    )
    return float(max(_R_MIN, min(_R_MAX, raw_reward)))


# ---------------------------------------------------------------------------
# Action validation (Fix #22)
# ---------------------------------------------------------------------------

def validate_action(action: Action) -> None:
    """
    Validate action is well-formed and applicable.

    Args:
        action: Action to validate.

    Raises:
        ValueError: If action is invalid.
    """
    if action is None:
        raise ValueError("Action cannot be None")
    
    if not isinstance(action, Action):
        raise ValueError(f"Action must be Action type, got {type(action)}")
    
    if action.action_type is None:
        raise ValueError("Action.action_type cannot be None")
    
    if not isinstance(action.action_type, ActionType):
        raise ValueError(f"action_type must be ActionType, got {type(action.action_type)}")


def get_replica_delta(action_type: ActionType) -> int:
    """
    Get replica count delta for a scale action.

    Args:
        action_type: Action type to analyze.

    Returns:
        int: Replica delta (positive=scale up, negative=scale down, 0=no change).
    """
    scale_map = {
        ActionType.SCALE_DOWN_5: -5,
        ActionType.SCALE_DOWN_1: -1,
        ActionType.SCALE_UP_1: 1,
        ActionType.SCALE_UP_5: 5,
        ActionType.SCALE_UP_10: 10,
        ActionType.SCALE_UP_20: 20,
        ActionType.UPGRADE_NODE: 0,
        ActionType.REBALANCE_NODE: 0,
        ActionType.MAINTAIN: 0,
    }
    return scale_map.get(action_type, 0)


class KubeCostEnv:
    """Kubernetes cost optimization environment.

    Implements OpenEnv interface for RL agents:
      - reset() → Observation
      - step(action) → (Observation, float, bool, dict)
      - state() → EnvState (typed, not dict)

    Uses deterministic pre-recorded traces for reproducibility.
    All dynamics are driven entirely by the loaded trace — no RNG.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, trace_path: str) -> None:
        """
        Initialize environment from deterministic trace.

        Args:
            trace_path: Path to JSON trace file
                       (e.g., 'traces/trace_v1_coldstart.json')

        Raises:
            FileNotFoundError: If trace_path not found
            ValueError: If trace schema is invalid
        """
        self.trace_path = Path(trace_path)
        self.trace: TraceData = load_trace(self.trace_path)
        self.steps_data = self.trace.steps  # list[TraceStep]
        self.total_steps: int = len(self.steps_data)

        # ------------------------------------------------------------------
        # Episode state — populated properly by reset()
        # ------------------------------------------------------------------
        self._step: int = 0
        self._current_obs: Observation | None = None

        # Mutable cluster state (updated by _apply_action)
        first_obs = self.steps_data[0].observation
        self._replicas: int = first_obs.active_replicas
        # Ensure node_size is an enum (Pydantic may return string value due to config)
        first_node = first_obs.node_size_class
        self._node_size: NodeSizeClass = NodeSizeClass(first_node) if isinstance(first_node, str) else first_node
        self._prev_steal_pct: float = first_obs.cpu_steal_pct

        # Trajectory log (list[TrajectoryStep]) — filled during step()
        self._trajectory: list[TrajectoryStep] = []

    # ------------------------------------------------------------------
    # OpenEnv Public Interface
    # ------------------------------------------------------------------

    def reset(self) -> Observation:
        """
        Reset environment to initial state.

        Returns:
            Observation: Typed Pydantic model instance (NOT dict).

        Resets the step counter to 0, re-seeds mutable cluster state from
        the very first trace step, and returns the matching Observation.
        """
        self._step = 0
        self._trajectory = []

        # Read starting cluster config from trace step 0
        first_trace_step = self.steps_data[0]
        first_obs = first_trace_step.observation

        self._replicas = first_obs.active_replicas
        # Ensure node_size is an enum (Pydantic may return string value due to config)
        first_node = first_obs.node_size_class
        self._node_size = NodeSizeClass(first_node) if isinstance(first_node, str) else first_node
        self._prev_steal_pct = first_obs.cpu_steal_pct

        # Set current observation (already a Pydantic model)
        self._current_obs = first_obs
        return self._current_obs

    def step(self, action: Action) -> Tuple[Observation, float, bool, Dict[str, Any]]:
        """
        Execute one step of environment dynamics.

        Args:
            action: Action model with action_type field.

        Returns:
            4-tuple: (Observation, float, bool, dict)
                - Observation: New state after action.
                - float:       Reward signal this step.
                - bool:        Episode done flag.
                - dict:        Info / metadata.

        Step semantics:
            1. Validate action.
            2. Advance step counter (next trace row provides new observation).
            3. Load new Observation from trace (physics ground-truth).
            4. Apply action to internal state.
            5. Compute reward from new observation.
            6. Determine done, return 4-tuple.
        """
        # 0. Validate action
        validate_action(action)
        
        # 1. Advance step counter FIRST
        self._step += 1
        
        # Prevent stepping beyond trace length
        if self._step >= self.total_steps:
            done = True
            return self._current_obs, 0.0, done, {}
        
        # 2. Load next observation from trace
        trace_idx = self._step
        new_obs = self.steps_data[trace_idx].observation
        
        # 3. Capture steal BEFORE reward calculation (for proactive bonus)
        if self._current_obs is not None:
            self._prev_steal_pct = self._current_obs.cpu_steal_pct
        
        # 4. Set new observation (observations are directly from trace, no formula overlays)
        self._current_obs = new_obs
        
        # 5. Apply action to internal state (replicas, node_size)
        self._apply_action(action)
        
        # 6. Compute reward (uses correct prev/curr steal comparison)
        reward: float = self._calculate_reward()
        
        # 7. Determine done
        done: bool = self._step >= self.total_steps - 1
        
        # 8. Build info dict
        trace_step = self.steps_data[trace_idx]
        info: Dict[str, Any] = {
            "step": self._step,
            "task_name": self.trace.task_name,
            "task_difficulty": self.trace.task_difficulty,
            "replicas": self._replicas,
            "node_size": self._node_size.value if isinstance(self._node_size, NodeSizeClass) else self._node_size,
            "trace_reason": trace_step.dynamics.get("reason", ""),
        }
        
        # 9. Log to trajectory (without redundant metrics)
        self._trajectory.append(
            TrajectoryStep(
                observation=self._current_obs,
                action=action.action_type,
                reward=reward,
                done=done,
                info=info,
            )
        )

        return self._current_obs, reward, done, info

    def state(self) -> EnvState:
        """
        Get current environment state snapshot.

        Returns:
            EnvState: Typed Pydantic model (NOT bare dict).
        """
        return EnvState(
            step=self._step,
            replicas=self._replicas,
            node_size=self._node_size,
            prev_steal_pct=self._prev_steal_pct,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_action(self, action: Action) -> None:
        """
        Apply action to environment state (internal helper, called by step).

        Translates the ActionType enum value into concrete state mutations:

        SCALE_REPLICAS(±N):
            self._replicas += N, clamped to [REPLICAS_MIN, REPLICAS_MAX].
        UPGRADE_NODE:
            Advances node tier by one level (S→M or M→L). Irreversible for 1 step
            (the trace physics will reflect the new size from the next step onward).
        REBALANCE_NODE:
            Sets a rebalance signal; no direct replica/node change. The agent earns
            the ProactiveBonus via _calculate_reward() if steal is dropping.
        MAINTAIN:
            No state mutation.

        Reference: PROJECT_SPEC.md §4 Action Space
        """
        action_type = action.action_type

        # ---- SCALE_REPLICAS branch ----
        if action_type == ActionType.SCALE_DOWN_5:
            self._replicas = max(_REPLICAS_MIN, self._replicas - 5)

        elif action_type == ActionType.SCALE_DOWN_1:
            self._replicas = max(_REPLICAS_MIN, self._replicas - 1)

        elif action_type == ActionType.SCALE_UP_1:
            self._replicas = min(_REPLICAS_MAX, self._replicas + 1)

        elif action_type == ActionType.SCALE_UP_5:
            self._replicas = min(_REPLICAS_MAX, self._replicas + 5)

        elif action_type == ActionType.SCALE_UP_10:
            self._replicas = min(_REPLICAS_MAX, self._replicas + 10)

        elif action_type == ActionType.SCALE_UP_20:
            # Audit Fix 04: emergency burst absorption for hard task
            self._replicas = min(_REPLICAS_MAX, self._replicas + 20)

        # ---- UPGRADE_NODE: irreversible for 1 step ----
        elif action_type == ActionType.UPGRADE_NODE:
            current_tier = _NODE_TIER[self._node_size]
            next_tier = min(current_tier + 1, len(_NODE_TIER) - 1)
            self._node_size = _NODE_FROM_TIER[next_tier]

        # ---- REBALANCE_NODE: proactive signal (no multi-step effect) ----
        elif action_type == ActionType.REBALANCE_NODE:
            # Rebalance action is logged in trajectory for grading
            # Graders use REBALANCE_NODE history to compute proactive scores
            pass

        # ---- MAINTAIN: explicit no-op ----
        elif action_type == ActionType.MAINTAIN:
            pass

    def _calculate_reward(self) -> float:
        """
        Calculate reward signal for the current step.

        Returns:
            float: Reward in range [-20.0, +10.5].
        """
        obs = self._current_obs
        if obs is None:
            return 0.0
        return compute_reward(obs, self._prev_steal_pct)

    # ------------------------------------------------------------------
    # Private utility
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    # Accessors (convenience)
    # ------------------------------------------------------------------

    @property
    def trajectory(self) -> list[TrajectoryStep]:
        """Read-only access to the episode trajectory log."""
        return list(self._trajectory)
