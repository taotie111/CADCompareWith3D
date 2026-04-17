import json
import math
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional

DIMENSION_KEYS = ("length_m", "width_m", "height_m")


def load_json(file_path: str) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(file_path: str, payload: Dict[str, Any]) -> None:
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def planar_distance_m(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return math.sqrt((float(a.get("x", 0)) - float(b.get("x", 0))) ** 2 + (float(a.get("y", 0)) - float(b.get("y", 0))) ** 2)


def relative_diff_ratio(design_value: float, reality_value: float) -> float:
    design_value = float(design_value)
    reality_value = float(reality_value)
    if design_value == 0:
        return abs(reality_value - design_value)
    return abs(reality_value - design_value) / abs(design_value)


def _threshold_hit(metric_value: float, threshold: float, comparison: str) -> bool:
    if comparison == ">":
        return metric_value > threshold
    if comparison == ">=":
        return metric_value >= threshold
    if comparison == "<":
        return metric_value < threshold
    if comparison == "<=":
        return metric_value <= threshold
    raise ValueError(f"Unsupported comparison: {comparison}")


def evaluate_level(metric_value: float, thresholds: Dict[str, float], comparison: str) -> str:
    for level in ("critical", "high", "medium", "low"):
        threshold = thresholds.get(level)
        if threshold is None:
            continue
        if _threshold_hit(metric_value, float(threshold), comparison):
            return level
    return ""


def compute_match_score(
    design_obj: Dict[str, Any],
    reality_obj: Dict[str, Any],
    distance_gate_m: float,
    source_weights: Dict[str, float],
) -> Tuple[float, Dict[str, float]]:
    d_id = design_obj.get("id")
    r_id = reality_obj.get("id")
    s_id = 1.0 if d_id and r_id and d_id == r_id else 0.0

    s_type = 1.0 if design_obj.get("type") == reality_obj.get("type") else 0.0

    dist = planar_distance_m(design_obj, reality_obj)
    if distance_gate_m <= 0:
        s_dist = 0.0
    else:
        s_dist = max(0.0, 1.0 - dist / distance_gate_m)

    dim_scores = []
    for key in DIMENSION_KEYS:
        if key in design_obj and key in reality_obj:
            ratio = relative_diff_ratio(float(design_obj[key]), float(reality_obj[key]))
            dim_scores.append(max(0.0, 1.0 - ratio))
    s_dim = sum(dim_scores) / len(dim_scores) if dim_scores else 0.7

    source_type = str(design_obj.get("source_type", "dwg")).lower()
    s_src = float(source_weights.get(source_type, 0.8))

    total = 0.35 * s_id + 0.20 * s_type + 0.20 * s_dist + 0.15 * s_dim + 0.10 * s_src

    return total, {
        "S_id": round(s_id, 4),
        "S_type": round(s_type, 4),
        "S_dist": round(s_dist, 4),
        "S_dim": round(s_dim, 4),
        "S_src": round(s_src, 4),
        "distance_m": round(dist, 4),
    }


def _avg_confidence(design_obj: Dict[str, Any], reality_obj: Dict[str, Any]) -> float:
    dc = float(design_obj.get("confidence", 1.0) or 1.0)
    rc = float(reality_obj.get("confidence", 1.0) or 1.0)
    return max(0.0, min(1.0, (dc + rc) / 2.0))


def match_objects(
    design_objects: List[Dict[str, Any]],
    reality_objects: List[Dict[str, Any]],
    max_match_distance_m: float = 10.0,
    min_match_score: float = 0.45,
    source_weights: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    返回：
    - matched: [{design, reality, metrics, match_confidence, match_reason}]
    - missing_design_objects
    - unplanned_reality_objects
    """
    source_weights = source_weights or {"dwg": 1.0, "pdf": 0.7}

    unused_reality = reality_objects.copy()
    matched = []
    missing_design = []

    for d_obj in design_objects:
        best_idx = -1
        best_score = -1.0
        best_reason = {}

        for idx, r_obj in enumerate(unused_reality):
            if d_obj.get("type") != r_obj.get("type") and d_obj.get("id") != r_obj.get("id"):
                continue

            score, reason = compute_match_score(d_obj, r_obj, max_match_distance_m, source_weights)
            if reason["distance_m"] > max_match_distance_m and reason["S_id"] < 1.0:
                continue

            if score > best_score:
                best_score = score
                best_idx = idx
                best_reason = reason

        if best_idx < 0 or best_score < min_match_score:
            missing_design.append(d_obj)
            continue

        candidate = unused_reality.pop(best_idx)

        dimension_deviation = {}
        max_dim_dev = 0.0
        for k in DIMENSION_KEYS:
            if k in d_obj and k in candidate:
                ratio = relative_diff_ratio(float(d_obj[k]), float(candidate[k]))
                dimension_deviation[k] = ratio
                max_dim_dev = max(max_dim_dev, ratio)

        metrics = {
            "planar_offset_m": planar_distance_m(d_obj, candidate),
            "elevation_deviation_m": abs(float(d_obj.get("z", 0)) - float(candidate.get("z", 0))),
            "dimension_deviation_rate": max_dim_dev,
            "dimension_deviation_detail": dimension_deviation,
            "deformation_m": float(candidate.get("deformation_m", 0) or 0),
            "deformation_area_m2": float(candidate.get("deformation_area_m2", 0) or 0),
        }

        confidence = round(max(0.0, min(1.0, best_score * _avg_confidence(d_obj, candidate))), 4)

        matched.append(
            {
                "design": d_obj,
                "reality": candidate,
                "metrics": metrics,
                "match_confidence": confidence,
                "match_reason": best_reason,
            }
        )

    unplanned_reality = unused_reality
    return matched, missing_design, unplanned_reality


def _event_base(rule: Dict[str, Any], level: str, location: str, evidence: Dict[str, Any], suggestion: str) -> Dict[str, Any]:
    return {
        "rule_id": rule.get("id"),
        "risk_type": rule.get("name"),
        "level": level,
        "location": location,
        "suggestion": suggestion,
        "evidence": evidence,
        "manual_review_required": False,
        "llm_reviewed": False,
        "review_source": "rule_engine",
    }


def _apply_risk_gate(event: Dict[str, Any], risk_gate: Dict[str, Any]) -> Dict[str, Any]:
    min_conf = float(risk_gate.get("min_confidence_for_auto_close", 0.75))
    force_levels = set(risk_gate.get("force_manual_review_levels", ["high", "critical"]))
    force_pdf_low_conf = bool(risk_gate.get("force_manual_review_when_pdf_low_conf", True))

    level = event.get("level")
    source_type = str(event.get("source_type", "dwg")).lower()
    source_conf = float(event.get("source_confidence", 1.0) or 1.0)

    manual = False
    if level in force_levels:
        manual = True
    if source_conf < min_conf:
        manual = True
    if force_pdf_low_conf and source_type == "pdf" and source_conf < min_conf:
        manual = True

    event["manual_review_required"] = manual
    return event


def _should_trigger_llm_review(event: Dict[str, Any], llm_policy: Dict[str, Any]) -> bool:
    if not llm_policy.get("enabled", False):
        return False

    mode = llm_policy.get("review_mode", "selective")
    if mode == "off":
        return False
    if mode == "always":
        return True

    triggers = llm_policy.get("triggers", {})
    low_match = bool(triggers.get("low_match_confidence", True))
    near_threshold = bool(triggers.get("pdf_source_near_threshold", True))
    conflict = bool(triggers.get("rule_conflict", True))

    if low_match and float(event.get("match_confidence", 1.0)) < float(llm_policy.get("low_match_threshold", 0.65)):
        return True

    if near_threshold and str(event.get("source_type", "dwg")).lower() == "pdf":
        metric_value = event.get("evidence", {}).get("metric_value")
        thresholds = event.get("evidence", {}).get("thresholds", {})
        if metric_value is not None and thresholds:
            all_thresholds = sorted(float(v) for v in thresholds.values())
            if all_thresholds:
                nearest = min(abs(float(metric_value) - t) for t in all_thresholds)
                if nearest <= float(llm_policy.get("near_threshold_margin", 0.02)):
                    return True

    if conflict and bool(event.get("rule_conflict", False)):
        return True

    return False


def run_diff_and_risk(
    design_data: Dict[str, Any],
    reality_data: Dict[str, Any],
    ruleset: Dict[str, Any],
    input_policy: Optional[Dict[str, Any]] = None,
    llm_policy: Optional[Dict[str, Any]] = None,
    risk_gate: Optional[Dict[str, Any]] = None,
    llm_reviewer: Optional[Any] = None,
) -> Dict[str, Any]:
    input_policy = input_policy or {}
    llm_policy = llm_policy or {"enabled": False, "review_mode": "off", "triggers": {}}
    risk_gate = risk_gate or {
        "min_confidence_for_auto_close": 0.75,
        "force_manual_review_levels": ["high", "critical"],
        "force_manual_review_when_pdf_low_conf": True,
    }

    design_objects = design_data.get("objects", [])
    reality_objects = reality_data.get("objects", [])

    source_weights = input_policy.get("source_weights", {"dwg": 1.0, "pdf": 0.7})

    matched, missing_design, unplanned_reality = match_objects(
        design_objects,
        reality_objects,
        max_match_distance_m=float(input_policy.get("max_match_distance_m", 10.0)),
        min_match_score=float(input_policy.get("min_match_score", 0.45)),
        source_weights=source_weights,
    )

    rules = {r.get("id"): r for r in ruleset.get("rules", [])}
    events: List[Dict[str, Any]] = []
    llm_reviews: List[Dict[str, Any]] = []

    for pair in matched:
        d_obj = pair["design"]
        r_obj = pair["reality"]
        metrics = pair["metrics"]
        location = d_obj.get("location", d_obj.get("id", "unknown"))

        source_type = str(d_obj.get("source_type", "dwg")).lower()
        source_conf = float(d_obj.get("confidence", 1.0) or 1.0)
        match_confidence = float(pair.get("match_confidence", 1.0))

        for rule_id, metric_name in (
            ("GEO_PLANAR_OFFSET", "planar_offset_m"),
            ("GEO_ELEVATION_DEVIATION", "elevation_deviation_m"),
            ("DIMENSION_DEVIATION_RATE", "dimension_deviation_rate"),
        ):
            rule = rules.get(rule_id)
            if not rule or not rule.get("enabled", True):
                continue

            metric_value = float(metrics.get(metric_name, 0))
            level = evaluate_level(metric_value, rule.get("thresholds", {}), rule.get("comparison", ">"))
            if not level:
                continue

            suggestion = (rule.get("action", {}) or {}).get(level, "现场复核")
            event = _event_base(
                rule,
                level,
                location,
                    {
                        "design_id": d_obj.get("id"),
                        "reality_id": r_obj.get("id"),
                        "metric": metric_name,
                        "metric_value": metric_value,
                        "thresholds": rule.get("thresholds", {}),
                        "dimension_detail": metrics.get("dimension_deviation_detail", {}),
                        "trace": d_obj.get("trace", {}),
                        "image_urls": (d_obj.get("trace", {}) or {}).get("image_urls", []),
                    },
                suggestion,
            )
            event["match_confidence"] = match_confidence
            event["source_type"] = source_type
            event["source_confidence"] = source_conf
            event["match_reason"] = pair.get("match_reason", {})

            event = _apply_risk_gate(event, risk_gate)

            if _should_trigger_llm_review(event, llm_policy) and llm_reviewer is not None:
                review_result = llm_reviewer.review_event(event)
                event["llm_reviewed"] = True
                event["review_source"] = review_result.get("review_source", "llm")
                event["llm_review"] = review_result
                if review_result.get("recommended_action"):
                    event["suggestion"] = review_result["recommended_action"]
                if isinstance(review_result.get("confidence_delta"), (float, int)):
                    event["source_confidence"] = max(0.0, min(1.0, event["source_confidence"] + float(review_result["confidence_delta"])))
                event = _apply_risk_gate(event, risk_gate)
                llm_reviews.append(review_result)

            events.append(event)

        deformation_rule = rules.get("STR_LOCAL_DEFORMATION")
        if deformation_rule and deformation_rule.get("enabled", True):
            deformation = float(metrics.get("deformation_m", 0))
            area = float(metrics.get("deformation_area_m2", 0))
            cond = deformation_rule.get("compound_conditions", {})
            min_deform = float(cond.get("min_deformation_m", 0))
            min_area = float(cond.get("min_area_m2", 0))

            if deformation >= min_deform and area >= min_area:
                level = evaluate_level(deformation, deformation_rule.get("thresholds", {}), deformation_rule.get("comparison", ">"))
                if level:
                    suggestion = (deformation_rule.get("action", {}) or {}).get(level, "现场复核")
                    event = _event_base(
                        deformation_rule,
                        level,
                        location,
                        {
                            "design_id": d_obj.get("id"),
                            "reality_id": r_obj.get("id"),
                            "deformation_m": deformation,
                            "area_m2": area,
                            "trace": d_obj.get("trace", {}),
                        },
                        suggestion,
                    )
                    event["match_confidence"] = match_confidence
                    event["source_type"] = source_type
                    event["source_confidence"] = source_conf
                    event["match_reason"] = pair.get("match_reason", {})
                    event = _apply_risk_gate(event, risk_gate)
                    events.append(event)

    missing_rule = rules.get("SEM_MISSING_CONSTRUCTION")
    missing_count = len(missing_design)
    if missing_rule and missing_rule.get("enabled", True):
        level = evaluate_level(float(missing_count), missing_rule.get("thresholds", {}), missing_rule.get("comparison", ">="))
        if level:
            suggestion = (missing_rule.get("action", {}) or {}).get(level, "补勘并确认工序")
            event = _event_base(
                missing_rule,
                level,
                "project_scope",
                {
                    "missing_count": missing_count,
                    "missing_design_ids": [x.get("id") for x in missing_design],
                },
                suggestion,
            )
            event["source_type"] = "mixed"
            event["source_confidence"] = 1.0
            event["match_confidence"] = 1.0
            event = _apply_risk_gate(event, risk_gate)
            events.append(event)

    unplanned_rule = rules.get("SEM_UNPLANNED_CONSTRUCTION")
    unplanned_count = len(unplanned_reality)
    if unplanned_rule and unplanned_rule.get("enabled", True):
        level = evaluate_level(float(unplanned_count), unplanned_rule.get("thresholds", {}), unplanned_rule.get("comparison", ">="))
        if level:
            suggestion = (unplanned_rule.get("action", {}) or {}).get(level, "核查设计变更")
            event = _event_base(
                unplanned_rule,
                level,
                "project_scope",
                {
                    "unplanned_count": unplanned_count,
                    "unplanned_reality_ids": [x.get("id") for x in unplanned_reality],
                },
                suggestion,
            )
            event["source_type"] = "mixed"
            event["source_confidence"] = 1.0
            event["match_confidence"] = 1.0
            event = _apply_risk_gate(event, risk_gate)
            events.append(event)

    level_stats = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    manual_review_count = 0
    for e in events:
        lv = e.get("level")
        if lv in level_stats:
            level_stats[lv] += 1
        if e.get("manual_review_required"):
            manual_review_count += 1

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "design_objects": len(design_objects),
            "reality_objects": len(reality_objects),
            "matched_objects": len(matched),
            "missing_objects": missing_count,
            "unplanned_objects": unplanned_count,
            "events_total": len(events),
            "events_by_level": level_stats,
            "manual_review_required": manual_review_count,
            "llm_reviews_total": len(llm_reviews),
        },
        "events": events,
        "llm_reviews": llm_reviews,
    }
