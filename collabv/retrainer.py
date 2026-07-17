"""
CollabV AI - Feedback Loop & Weight Retraining Pipeline
==========================================================
Analyzes user feedback (accept/reject) to retune the matching engine weights.

Algorithm:
  - Pull every match result and its feedback record(s).
  - For each scoring factor, compute mean accepted vs mean rejected.
  - Run a constrained Nelder-Mead optimization to find weights that maximize the
    gap between accepted and rejected composite scores, subject to:
      * weights sum to 1.0
      * each weight in [0.05, 0.50]
  - Cap the learning rate so weights don't swing more than 0.10 per retrain.

Public API:
    WeightRetrainer(db_path).analyze_feedback() -> FeedbackAnalysis
    WeightRetrainer(db_path).retrain_weights() -> WeightUpdate
    WeightRetrainer(db_path).simulate_weights(new_weights) -> SimulationResult
    load_weights() / save_weights()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Default factors & weights ──────────────────────────────────────────────

DEFAULT_FACTORS: List[str] = [
    "tier1_score",
    "tier2_score",
    "tier3_score",
    "patent_score",
    "readiness_score",
]

DEFAULT_WEIGHTS: Dict[str, float] = {
    "tier1_score": 0.45,
    "tier2_score": 0.30,
    "tier3_score": 0.05,
    "patent_score": 0.10,
    "readiness_score": 0.10,
}

WEIGHTS_FILE = Path(__file__).parent.parent / "collabv_weights.json"
MIN_FEEDBACK_THRESHOLD = 30


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class FeedbackAnalysis:
    total_feedback: int
    accept_count: int
    reject_count: int
    accept_rate: float
    reject_rate: float
    most_accepted_departments: List[Tuple[str, int]] = field(default_factory=list)
    most_rejected_reasons: List[Tuple[str, int]] = field(default_factory=list)
    factor_correlation: Dict[str, float] = field(default_factory=dict)
    sufficient_data: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["most_accepted_departments"] = [list(t) for t in self.most_accepted_departments]
        d["most_rejected_reasons"] = [list(t) for t in self.most_rejected_reasons]
        return d


@dataclass
class WeightUpdate:
    old_weights: Dict[str, float]
    new_weights: Dict[str, float]
    improvement_score: float
    applied_at: str
    feedback_count_used: int
    accepted_mean_before: float
    rejected_mean_before: float
    accepted_mean_after: float
    rejected_mean_after: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SimulationResult:
    weights: Dict[str, float]
    accepted_mean: float
    rejected_mean: float
    gap: float
    note: str = ""


# ─── Weight persistence ─────────────────────────────────────────────────────

def load_weights(path: Optional[Path] = None) -> Dict[str, float]:
    path = path or WEIGHTS_FILE
    if not path.exists():
        return dict(DEFAULT_WEIGHTS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            # Ensure all factors present
            merged = dict(DEFAULT_WEIGHTS)
            for k, v in data.items():
                if k in merged:
                    merged[k] = float(v)
            return _normalize(merged)
    except Exception as e:
        logger.warning("Failed to load weights from %s: %s", path, e)
    return dict(DEFAULT_WEIGHTS)


def save_weights(weights: Dict[str, float], path: Optional[Path] = None) -> None:
    path = path or WEIGHTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2)


def _normalize(weights: Dict[str, float]) -> Dict[str, float]:
    total = sum(weights.values()) or 1.0
    return {k: v / total for k, v in weights.items()}


# ─── Weight history table ──────────────────────────────────────────────────

def init_weight_history(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weight_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weights_json TEXT NOT NULL,
                improvement_score REAL,
                feedback_count INTEGER,
                applied_at REAL NOT NULL,
                note TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ─── Retrainer ──────────────────────────────────────────────────────────────

class WeightRetrainer:
    """Analyze feedback and retrain matching engine weights."""

    def __init__(self, db_path: str, weights_path: Optional[Path] = None) -> None:
        self.db_path = db_path
        self.weights_path = weights_path or WEIGHTS_FILE
        init_weight_history(db_path)

    # ─── Analysis ───────────────────────────────────────────────────────────

    def analyze_feedback(self) -> FeedbackAnalysis:
        records = self._load_feedback_records()
        if not records:
            return FeedbackAnalysis(
                total_feedback=0, accept_count=0, reject_count=0,
                accept_rate=0.0, reject_rate=0.0, sufficient_data=False,
            )

        accepts = [r for r in records if r["is_accept"]]
        rejects = [r for r in records if r["is_reject"]]

        # Department stats
        dept_accepts: Dict[str, int] = {}
        for r in accepts:
            d = r.get("department", "Unknown")
            dept_accepts[d] = dept_accepts.get(d, 0) + 1
        top_depts = sorted(dept_accepts.items(), key=lambda x: -x[1])[:8]

        # Reject reason stats
        reason_counts: Dict[str, int] = {}
        for r in rejects:
            reason = (r.get("reason") or "no reason given").strip().lower() or "no reason given"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        top_reasons = sorted(reason_counts.items(), key=lambda x: -x[1])[:8]

        # Factor correlation: difference of means between accepts and rejects
        correlation: Dict[str, float] = {}
        for factor in DEFAULT_FACTORS:
            a_vals = [r["factors"].get(factor, 0.0) for r in accepts if factor in r["factors"]]
            r_vals = [r["factors"].get(factor, 0.0) for r in rejects if factor in r["factors"]]
            if a_vals and r_vals:
                correlation[factor] = round(
                    (sum(a_vals) / len(a_vals)) - (sum(r_vals) / len(r_vals)), 2
                )

        total = len(records)
        return FeedbackAnalysis(
            total_feedback=total,
            accept_count=len(accepts),
            reject_count=len(rejects),
            accept_rate=round(len(accepts) / total, 3) if total else 0,
            reject_rate=round(len(rejects) / total, 3) if total else 0,
            most_accepted_departments=top_depts,
            most_rejected_reasons=top_reasons,
            factor_correlation=correlation,
            sufficient_data=total >= MIN_FEEDBACK_THRESHOLD,
        )

    # ─── Retraining ─────────────────────────────────────────────────────────

    def retrain_weights(self) -> WeightUpdate:
        records = self._load_feedback_records()
        analysis = self.analyze_feedback()

        old_weights = load_weights(self.weights_path)
        applied_at = datetime.now().isoformat(timespec="seconds")

        if analysis.total_feedback < MIN_FEEDBACK_THRESHOLD:
            return WeightUpdate(
                old_weights=old_weights,
                new_weights=old_weights,
                improvement_score=0.0,
                applied_at=applied_at,
                feedback_count_used=analysis.total_feedback,
                accepted_mean_before=0.0,
                rejected_mean_before=0.0,
                accepted_mean_after=0.0,
                rejected_mean_after=0.0,
                note=f"Need at least {MIN_FEEDBACK_THRESHOLD} feedback records to retrain (got {analysis.total_feedback}).",
            )

        accepts = [r for r in records if r["is_accept"]]
        rejects = [r for r in records if r["is_reject"]]
        if not accepts or not rejects:
            return WeightUpdate(
                old_weights=old_weights,
                new_weights=old_weights,
                improvement_score=0.0,
                applied_at=applied_at,
                feedback_count_used=analysis.total_feedback,
                accepted_mean_before=0.0,
                rejected_mean_before=0.0,
                accepted_mean_after=0.0,
                rejected_mean_after=0.0,
                note="Need both accepted and rejected examples to retrain.",
            )

        before_a, before_r, _ = self._mean_gap(old_weights, accepts, rejects)
        proposed = self._optimize(old_weights, accepts, rejects)

        # Limit per-retrain movement to 0.10
        limited = self._limit_movement(old_weights, proposed, max_step=0.10)
        after_a, after_r, _ = self._mean_gap(limited, accepts, rejects)

        gap_before = before_a - before_r
        gap_after = after_a - after_r
        improvement = round(gap_after - gap_before, 2)

        new_weights = limited if improvement > 0 else old_weights
        if improvement > 0:
            save_weights(new_weights, self.weights_path)

        # Persist to history
        self._save_history(new_weights, improvement, analysis.total_feedback,
                           note="auto-retrain" if improvement > 0 else "no improvement; weights unchanged")

        return WeightUpdate(
            old_weights=old_weights,
            new_weights=new_weights,
            improvement_score=improvement,
            applied_at=applied_at,
            feedback_count_used=analysis.total_feedback,
            accepted_mean_before=round(before_a, 2),
            rejected_mean_before=round(before_r, 2),
            accepted_mean_after=round(after_a, 2),
            rejected_mean_after=round(after_r, 2),
            note=f"Gap before={gap_before:.2f}, after={gap_after:.2f}",
        )

    def simulate_weights(self, new_weights: Dict[str, float]) -> SimulationResult:
        normalized = _normalize({**DEFAULT_WEIGHTS, **new_weights})
        records = self._load_feedback_records()
        accepts = [r for r in records if r["is_accept"]]
        rejects = [r for r in records if r["is_reject"]]
        if not accepts or not rejects:
            return SimulationResult(
                weights=normalized, accepted_mean=0.0, rejected_mean=0.0, gap=0.0,
                note="Insufficient data",
            )
        a, r, _ = self._mean_gap(normalized, accepts, rejects)
        return SimulationResult(
            weights=normalized,
            accepted_mean=round(a, 2),
            rejected_mean=round(r, 2),
            gap=round(a - r, 2),
        )

    def get_weight_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT id, weights_json, improvement_score, feedback_count, applied_at, note
                   FROM weight_history
                   ORDER BY applied_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": r[0],
                "weights": json.loads(r[1]),
                "improvement_score": r[2],
                "feedback_count": r[3],
                "applied_at": r[4],
                "note": r[5],
            }
            for r in rows
        ]

    def rollback(self) -> Optional[Dict[str, float]]:
        history = self.get_weight_history(limit=2)
        if len(history) < 2:
            return None
        previous = history[1]["weights"]
        save_weights(previous, self.weights_path)
        self._save_history(previous, 0.0, 0, note="rollback")
        return previous

    # ─── Internals ──────────────────────────────────────────────────────────

    def _load_feedback_records(self) -> List[Dict[str, Any]]:
        """Join feedback to match_results and extract per-record factor scores."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT f.match_id, f.professor_id, f.action, f.reason, m.results_json
                   FROM feedback f
                   LEFT JOIN match_results m ON f.match_id = m.match_id"""
            ).fetchall()
        finally:
            conn.close()

        records: List[Dict[str, Any]] = []
        for row in rows:
            action = (row["action"] or "").lower()
            is_accept = action in {"accept", "accepted", "yes"}
            is_reject = action in {"reject", "rejected", "no", "not_interested"}
            if not (is_accept or is_reject):
                continue
            if not row["results_json"]:
                continue
            try:
                results = json.loads(row["results_json"])
            except Exception:
                continue
            # Find the match record for this professor
            match = next((m for m in results if str(m.get("professor_id")) == row["professor_id"]), None)
            if not match:
                continue
            records.append({
                "is_accept": is_accept,
                "is_reject": is_reject,
                "reason": row["reason"],
                "department": match.get("department", "Unknown"),
                "factors": {f: float(match.get(f, 0) or 0) for f in DEFAULT_FACTORS},
            })
        return records

    @staticmethod
    def _composite(weights: Dict[str, float], factors: Dict[str, float]) -> float:
        return sum(weights[k] * factors.get(k, 0.0) for k in weights)

    def _mean_gap(
        self, weights: Dict[str, float], accepts: List[Dict[str, Any]], rejects: List[Dict[str, Any]],
    ) -> Tuple[float, float, float]:
        a_scores = [self._composite(weights, r["factors"]) for r in accepts]
        r_scores = [self._composite(weights, r["factors"]) for r in rejects]
        a_mean = sum(a_scores) / max(len(a_scores), 1)
        r_mean = sum(r_scores) / max(len(r_scores), 1)
        return a_mean, r_mean, a_mean - r_mean

    def _optimize(
        self,
        starting_weights: Dict[str, float],
        accepts: List[Dict[str, Any]],
        rejects: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """Gradient-free hill climbing with projection to the simplex.

        We use simple coordinate ascent rather than scipy's minimize because the
        objective is non-smooth (means of step functions), bound-constrained,
        and tiny enough that brute-force coord climbing is more reliable than
        Nelder-Mead in fewer-than-10 dimensions.
        """
        keys = list(starting_weights.keys())
        weights = dict(starting_weights)

        # Try scipy's optimization first if available
        try:
            from scipy.optimize import minimize  # type: ignore

            def neg_gap(x):
                w = dict(zip(keys, x))
                w = self._project_simplex(w)
                a, r, gap = self._mean_gap(w, accepts, rejects)
                return -gap

            x0 = [weights[k] for k in keys]
            res = minimize(
                neg_gap, x0, method="Nelder-Mead",
                options={"xatol": 1e-3, "fatol": 1e-3, "maxiter": 200},
            )
            if res.success:
                proposed = self._project_simplex(dict(zip(keys, res.x)))
                a, r, gap = self._mean_gap(proposed, accepts, rejects)
                _, _, base_gap = self._mean_gap(starting_weights, accepts, rejects)
                if gap >= base_gap:
                    return proposed
        except Exception as e:
            logger.debug("scipy minimize unavailable: %s", e)

        # Fallback: coordinate ascent
        best_gap = self._mean_gap(weights, accepts, rejects)[2]
        for _ in range(30):
            improved = False
            for k in keys:
                for delta in (0.05, -0.05, 0.02, -0.02):
                    candidate = dict(weights)
                    candidate[k] = weights[k] + delta
                    candidate = self._project_simplex(candidate)
                    gap = self._mean_gap(candidate, accepts, rejects)[2]
                    if gap > best_gap + 1e-4:
                        weights = candidate
                        best_gap = gap
                        improved = True
                        break
                if improved:
                    break
            if not improved:
                break
        return weights

    @staticmethod
    def _project_simplex(weights: Dict[str, float]) -> Dict[str, float]:
        clipped = {k: max(0.05, min(0.50, v)) for k, v in weights.items()}
        total = sum(clipped.values())
        if total == 0:
            return clipped
        return {k: v / total for k, v in clipped.items()}

    @staticmethod
    def _limit_movement(
        old: Dict[str, float], new: Dict[str, float], max_step: float = 0.10,
    ) -> Dict[str, float]:
        result = {}
        for k, v in new.items():
            o = old.get(k, v)
            delta = max(-max_step, min(max_step, v - o))
            result[k] = o + delta
        return _normalize(result)

    def _save_history(
        self, weights: Dict[str, float], improvement: float, feedback_count: int, note: str = "",
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO weight_history
                   (weights_json, improvement_score, feedback_count, applied_at, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (json.dumps(weights), improvement, feedback_count, time.time(), note),
            )
            conn.commit()
        finally:
            conn.close()


# ─── CLI ────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="CollabV AI retraining tool")
    parser.add_argument("--db", default=str(Path(__file__).parent.parent / "collabv_data.db"))
    parser.add_argument("--action", choices=["analyze", "retrain", "rollback", "history"], default="analyze")
    args = parser.parse_args()

    rt = WeightRetrainer(args.db)
    if args.action == "analyze":
        analysis = rt.analyze_feedback()
        print(json.dumps(analysis.to_dict(), indent=2))
    elif args.action == "retrain":
        update = rt.retrain_weights()
        print(json.dumps(update.to_dict(), indent=2))
    elif args.action == "rollback":
        prev = rt.rollback()
        print(json.dumps(prev or {}, indent=2))
    else:
        history = rt.get_weight_history()
        print(json.dumps(history, indent=2))


if __name__ == "__main__":
    _main()


__all__ = [
    "WeightRetrainer",
    "FeedbackAnalysis",
    "WeightUpdate",
    "SimulationResult",
    "load_weights",
    "save_weights",
    "DEFAULT_WEIGHTS",
    "DEFAULT_FACTORS",
]
