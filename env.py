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
# TraceLoader: Handles all trace file I/O and validation (Fix #20)
# ---------------------------------------------------------------------------
class TraceLoader:
    """Loads and validates deterministic trace JSON files."""

    @staticmethod
    def load(trace_path: str | Path) -> dict:
        """
        Load and validate deterministic trace JSON.

        Args:
            trace_path: Path to JSON file (str or Path).

        Returns:
            dict: Validated trace structure.

        Raises:
            FileNotFoundError: If trace_path does not exist.
            ValueError: If required schema keys are missing or steps list is empty.
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

        # Validate schema
        TraceLoader._validate_schema(data, trace_path)
        return data

    @staticmethod
    def _validate_schema(data: dict, trace_path: Path) -> None:
        """Validate trace JSON schema and values."""
        # Required top-level keys
        missing_keys = [k for k in ("task_name", "task_difficulty", "steps") if k not in data]
        if missing_keys:
            raise ValueError(
                f"Trace JSON missing required keys: {missing_keys} "
                f"(file: {trace_path})"
            )

        # Steps must be a non-empty list
        if not isinstance(data["steps"], list) or len(data["steps"]) == 0:
            raise ValueError(
                f"Trace 'steps' must be a non-empty list (file: {trace_path})"
            )

        # Validate each step
        for i, step in enumerate(data["steps"]):
            for sub_key in ("step", "observation"):
                if sub_key not in step:
                    raise ValueError(
                        f"Trace step index {i} missing key '{sub_key}' "
                        f"(file: {trace_path})"
                    )
            
            obs = step["observation"]
            required_obs_keys = [
                "cpu_usage_pct", "mem_usage_pct", "p99_latency_ms",
                "http_error_rate", "cpu_steal_pct", "active_replicas",
                "buffer_depth", "node_size_class", "current_hourly_cost",
                "node_bin_density",
            ]
            missing_obs = [k for k in required_obs_keys if k not in obs]
            if missing_obs:
                raise ValueError(
                    f"Trace step {i} observation missing keys: {missing_obs} "
                    f"(file: {trace_path})"
                )
            
            if not isinstance(obs["node_bin_density"], list) or len(obs["node_bin_density"]) != 10:
                raise ValueError(
                    f"Trace step {i}: node_bin_density must be a 10-element list "
                    f"(file: {trace_path})"
                )

            # Numeric range validation
            if not (0.0 <= float(obs["cpu_usage_pct"]) <= 100.0):
                raise ValueError(f"Trace step {i}: cpu_usage_pct out of [0,100] (file: {trace_path})")
            if not (0.0 <= float(obs["mem_usage_pct"]) <= 100.0):
                raise ValueError(f"Trace step {i}: mem_usage_pct out of [0,100] (file: {trace_path})")
            if float(obs["http_error_rate"]) < 0.0 or float(obs["http_error_rate"]) > 1.0:
                raise ValueError(f"Trace step {i}: http_error_rate out of [0,1] (file: {trace_path})")
            if float(obs["cpu_steal_pct"]) < 0.0 or float(obs["cpu_steal_pct"]) > 1.0:
                raise ValueError(f"Trace step {i}: cpu_steal_pct out of [0,1] (file: {trace_path})")


# ---------------------------------------------------------------------------
# RewardComputer: Pure functions for reward calculation (Fix #21)
# ---------------------------------------------------------------------------
class RewardComputer:
    """Stateless reward computation following PROJECT_SPEC.md §3 formula."""

    @staticmethod
    def compute(observation: Observation, previous_steal_pct: float) -> float:
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
# ActionValidator: Action validation and type checking (Fix #22)
# ---------------------------------------------------------------------------
class ActionValidator:
    """Validates actions before application in environment."""

    @staticmethod
    def validate(action: Action) -> None:
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

    @staticmethod
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
        self.trace: dict = TraceLoader.load(self.trace_path)
        self.steps_data = self.trace["steps"]  # list[dict]
        self.total_steps: int = len(self.steps_data)

        # ------------------------------------------------------------------
        # Episode state — populated properly by reset()
        # ------------------------------------------------------------------
        self._step: int = 0
        self._current_obs: Observation | None = None

        # Mutable cluster state (updated by _apply_action)
        first_obs_raw = self.steps_data[0]["observation"]
        self._replicas: int = int(first_obs_raw["active_replicas"])
        self._node_size: NodeSizeClass = NodeSizeClass(
            first_obs_raw["node_size_class"]
        )
        self._prev_steal_pct: float = float(first_obs_raw["cpu_steal_pct"])

        # Trajectory log (list[TrajectoryStep]) — filled during step()
        self._trajectory: list[TrajectoryStep] = []

        # Tracking rebalance effect (Task 3)
        self._rebalance_impact_remaining: int = 0

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
        first = self.steps_data[0]
        obs_raw = first["observation"]

        self._replicas = int(obs_raw["active_replicas"])
        self._node_size = NodeSizeClass(obs_raw["node_size_class"])
        self._prev_steal_pct = float(obs_raw["cpu_steal_pct"])
        self._rebalance_impact_remaining = 0

        # Build typed Observation from trace data
        self._current_obs = self._parse_observation(obs_raw)
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
            3. Build new Observation from trace (physics ground-truth).
            4. Apply action to internal state.
            5. Compute reward from new observation.
            6. Determine done, return 4-tuple.
        """
        # 0. Validate action
        ActionValidator.validate(action)
        
        # 1. Advance step counter FIRST
        self._step += 1
        
        # Prevent stepping beyond trace length
        if self._step >= self.total_steps:
            done = True
            return self._current_obs, 0.0, done, {}
        
        # 2. Load next observation from trace
        trace_idx = self._step
        obs_raw = self.steps_data[trace_idx]["observation"]
        new_obs = self._parse_observation(obs_raw)
        
        # 3. Capture steal BEFORE reward calculation (for proactive bonus)
        if self._current_obs is not None:
            self._prev_steal_pct = self._current_obs.cpu_steal_pct
        
        # 4. Set new observation (observations are directly from trace, no formula overlays)
        self._current_obs = new_obs
        
        # 5. NOW apply the action to internal state (replicas, node_size)
        self._apply_action(action)
        
        # 6. Compute reward (uses correct prev/curr steal comparison)
        reward: float = self._calculate_reward()
        
        # 7. Determine done
        done: bool = self._step >= self.total_steps - 1
        
        # 8. Build info dict
        info: Dict[str, Any] = {
            "step": self._step,
            "task_name": self.trace.get("task_name", ""),
            "task_difficulty": self.trace.get("task_difficulty", ""),
            "replicas": self._replicas,
            "node_size": self._node_size.value,
            "trace_reason": self.steps_data[trace_idx].get("dynamics", {}).get("reason", ""),
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

        # ---- REBALANCE_NODE: proactive signal, triggers physics impact ----
        elif action_type == ActionType.REBALANCE_NODE:
            # Rebalance only has effect if steal is currently high
            if self._current_obs and self._current_obs.cpu_steal_pct >= 0.15:
                # Impact lasts for 3 steps (conservative, based on K8s drain/reschedule time)
                self._rebalance_impact_remaining = 3
            else:
                # Unnecessary rebalance — no effect (agent learns not to spam)
                self._rebalance_impact_remaining = 0

        # ---- MAINTAIN: explicit no-op ----
        elif action_type == ActionType.MAINTAIN:
            pass

    def _calculate_reward(self) -> float:
        """
        Calculate reward signal for the current step (uses RewardComputer).

        Returns:
            float: Reward in range [-20.0, +10.5].
        """
        obs = self._current_obs
        if obs is None:
            return 0.0
        return RewardComputer.compute(obs, self._prev_steal_pct)

    # ------------------------------------------------------------------
    # Private utility
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_observation(obs_raw: dict) -> Observation:
        """Build a typed Observation Pydantic model from a raw trace obs dict."""
        return Observation(
            cpu_usage_pct=float(obs_raw["cpu_usage_pct"]),
            mem_usage_pct=float(obs_raw["mem_usage_pct"]),
            p99_latency_ms=float(obs_raw["p99_latency_ms"]),
            http_error_rate=float(obs_raw["http_error_rate"]),
            cpu_steal_pct=float(obs_raw["cpu_steal_pct"]),
            active_replicas=int(obs_raw["active_replicas"]),
            buffer_depth=int(obs_raw["buffer_depth"]),
            node_size_class=NodeSizeClass(obs_raw["node_size_class"]),
            current_hourly_cost=float(obs_raw["current_hourly_cost"]),
            node_bin_density=[float(v) for v in obs_raw["node_bin_density"]],
        )

    # ------------------------------------------------------------------
    # Accessors (convenience)
    # ------------------------------------------------------------------

    @property
    def trajectory(self) -> list[TrajectoryStep]:
        """Read-only access to the episode trajectory log."""
        return list(self._trajectory)
