# graders.py
"""
Grader implementations (Phase 4: Grader Spec).

Each grader:
  1. Has mathematical formula (documented in docstring)
  2. Returns float in [0.0, 1.0] (CRITICAL: must be bounded)
  3. Normalizes by trajectory length (not unbounded accumulation)
  4. Handles empty trajectory explicitly

Reference: PROJECT_SPEC.md §3 Phase 4, §6 The Three Tasks
"""

from typing import List
from models import TrajectoryStep


class ColdStartGrader:
    """
    Task 1: Cold Start (Easy).
    
    Objective: Scale cluster from 0→5 replicas without SLA breach.
    
    Formula (mathematical notation):
        score = 1.0 - http_error_rate_avg
        final_score = max(0.0, min(1.0, score))
    
    Normalization:
        By average error rate (length-invariant)
        Same error rate → same score regardless of episode length
    
    Edge case:
        Empty trajectory → return 0.0 explicitly
    
    Reference:
        PROJECT_SPEC.md §6 Task 1 Cold Start
        PROJECT_SPEC.md §3 Phase 4 Grader Spec
        Audit Fix 02: Normalized (not unbounded -= 0.05)
    
    Common Failure (spec audit fix 02):
        ✗ score = 1.0; score -= 0.05 per violation
           (unbounded, length-dependent)
        ✓ score = 1.0 - (violations / len(trajectory))
           (normalized, length-invariant)
    """
    
    def grade(self, trajectory: List[TrajectoryStep]) -> float:
        """
        Grade cold start performance.
        
        Args:
            trajectory: List of trajectory steps
        
        Returns:
            float: Score in [0.0, 1.0] (strictly bounded)
        """
        # Empty trajectory → 0.0
        if not trajectory:
            return 0.0
        
        # Compute average http_error_rate
        error_rates = [step.observation.http_error_rate for step in trajectory]
        avg_error_rate = sum(error_rates) / len(error_rates) if error_rates else 0.0
        
        # Score formula: 1.0 - avg_error_rate
        score = 1.0 - avg_error_rate
        
        # CRITICAL: Strictly clamp to [0.0, 1.0]
        return max(0.0, min(1.0, score))


class EfficientSqueezeGrader:
    """
    Task 2: Efficient Squeeze (Medium).
    
    Objective: Maintain cpu_steal_pct < 20% across 24-hour sine-wave load cycle.
    
    Formula (mathematical notation):
        violations = count(steps where cpu_steal_pct >= 0.20)
        score = 1.0 - (violations / len(trajectory))
        final_score = max(0.0, min(1.0, score))
    
    Normalization:
        By violation count per trajectory length
        Same violation rate → same normalized score
        E.g., 10 violations in 100 steps = 20 violations in 200 steps
    
    Edge case:
        Empty trajectory → return 0.0 explicitly
    
    Reference:
        PROJECT_SPEC.md §6 Task 2 Efficient Squeeze
        PROJECT_SPEC.md §3 Phase 4 Grader Spec
        Audit Fix 02: Normalized by len(trajectory)
    
    Key Insight:
        Violation rate (0.0 = perfect, 1.0 = all steps violated) is invariant
        to trajectory length. This ensures fair comparison across different
        simulation durations (24h vs 48h traces).
    
    Float Comparison (spec audit fix 01):
        ✗ cpu_steal_pct == 0.20
        ✓ cpu_steal_pct >= 0.20 (or < 0.20 for threshold)
    """
    
    def grade(self, trajectory: List[TrajectoryStep]) -> float:
        """
        Grade efficient squeeze performance.
        
        Args:
            trajectory: List of trajectory steps
        
        Returns:
            float: Score in [0.0, 1.0] (strictly bounded)
        """
        # Empty trajectory → 0.0
        if not trajectory:
            return 0.0
        
        # Count violations: cpu_steal_pct >= 0.20
        violations = sum(
            1 for step in trajectory 
            if step.observation.cpu_steal_pct >= 0.20
        )
        
        # Score formula: 1.0 - (violations / len(trajectory))
        score = 1.0 - (violations / len(trajectory))
        
        # CRITICAL: Strictly clamp to [0.0, 1.0]
        return max(0.0, min(1.0, score))


class EntropyStormGrader:
    """
    Task 3: Entropy Storm (Hard).
    
    Objective: Issue REBALANCE_NODE BEFORE steal exceeds 20% (proactive reasoning).
    
    Formula (mathematical notation):
        1. Identify violations: steps where cpu_steal_pct >= 0.20
        2. For each violation at step i:
           - Check if REBALANCE_NODE occurred in steps [max(0, i-k), i-1]
             (lookback window of k steps; spec: k=TBD)
           - If yes: proactive_count += 1
           - If no: failure_count += 1
        3. success_rate = proactive_count / max(1, total_violations)
        4. score = success_rate × 1.0 + cost_bonus
        5. final_score = max(0.0, min(1.0, score))
    
    Special cases:
        - No violations (steal never >= 0.20): score = 1.0 (agent won)
        - Empty trajectory: score = 0.0
    
    Normalization:
        By count of total violations
        Same prediction accuracy → same score
    
    Reference:
        PROJECT_SPEC.md §6 Task 3 Entropy Storm
        PROJECT_SPEC.md §3 Phase 4 Grader Spec, §5 Audit Fix 04
    
    Key Insight (Audit Fix 04 & Task Design):
        This is the ONLY task where reactive scaling (AFTER p99 breach)
        cannot achieve high score. Reactive agents see violation AFTER it happens;
        cannot undo it. Agent MUST learn to predict and act BEFORE the
        leading indicator (cpu_steal_pct) rises above 20%.
        
        Tests for genuine proactive reasoning, not just reactive optimization.
    
    Design Challenge:
        If ActionType missing SCALE_REPLICAS(+20), hard task becomes structurally
        unsolvable (no action sequence achieves 5x replica increase needed for
        emergency burst). Verify ActionType complete before implementation.
    """
    
    def grade(self, trajectory: List[TrajectoryStep]) -> float:
        """
        Grade entropy storm (proactive rebalancing) performance.
        
        Args:
            trajectory: List of trajectory steps
        
        Returns:
            float: Score in [0.0, 1.0] (strictly bounded)
        """
        # Empty trajectory → 0.0
        if not trajectory:
            return 0.0
        
        # Identify violations: cpu_steal_pct >= 0.20
        violations = [
            i for i, step in enumerate(trajectory)
            if step.observation.cpu_steal_pct >= 0.20
        ]
        
        # No violations → 1.0 (agent avoided the problem)
        if not violations:
            return 1.0
        
        # For each violation, check if REBALANCE_NODE occurred in lookback window
        proactive_count = 0
        lookback_window = 5  # Spec: look back up to 5 steps before violation
        
        for violation_idx in violations:
            # Check actions in window [max(0, violation_idx - lookback_window), violation_idx - 1]
            window_start = max(0, violation_idx - lookback_window)
            window_steps = trajectory[window_start:violation_idx]
            
            # Check if REBALANCE_NODE occurred in this window
            if any(step.action == ActionType.REBALANCE_NODE for step in window_steps):
                proactive_count += 1
        
        # Success rate: proactive_count / total_violations
        success_rate = proactive_count / max(1, len(violations))
        score = success_rate
        
        # CRITICAL: Strictly clamp to [0.0, 1.0]
        return max(0.0, min(1.0, score))
