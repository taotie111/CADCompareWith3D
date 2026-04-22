import json
from typing import Any, Dict


DEFAULT_POLICY: Dict[str, Any] = {
    "input_policy": {
        "dwg_enabled": True,
        "pdf_enabled": True,
        "pdf_mode": "optional",
        "pdf_skip_allowed": True,
        "prefer_source": "dwg",
        "max_match_distance_m": 10.0,
        "min_match_score": 0.45,
        "source_weights": {"dwg": 1.0, "pdf": 0.7},
        "dwg": {
            "prefer_tool": "auto",
            "tool_timeout_sec": 300,
            "default_coverage": 0.8,
            "dwgread_candidates": [],
            "ogr2ogr_candidates": [],
            "registration": {
                "mode": "auto",
                "auto_scale_candidates": [1.0, 0.1, 0.01, 0.001],
                "min_overlap_ratio_to_skip": 0.05,
                "min_improve_ratio": 0.2,
                "manual_transform": {
                    "scale_x": 1.0,
                    "scale_y": 1.0,
                    "dx": 0.0,
                    "dy": 0.0,
                },
            },
            "layer_mapping": {
                "enabled": True,
                "unknown_type": "unknown",
                "ignore_layers": ["0", "DEFPOINTS", "标注"],
                "rules": [
                    {"match": "regex", "pattern": "(?i)spillway|溢洪道", "type": "spillway"},
                    {"match": "regex", "pattern": "(?i)gate|闸室", "type": "gate_chamber"},
                    {"match": "regex", "pattern": "(?i)tunnel|导流洞", "type": "tunnel"},
                    {"match": "regex", "pattern": "(?i)panel|面板坝", "type": "panel_dam"},
                    {"match": "regex", "pattern": "(?i)supply|供水", "type": "supply"},
                ],
            },
        },
    },
    "llm_policy": {
        "enabled": False,
        "review_mode": "off",
        "low_match_threshold": 0.65,
        "near_threshold_margin": 0.02,
        "triggers": {
            "low_match_confidence": True,
            "rule_conflict": True,
            "pdf_source_near_threshold": True,
        },
        "output_schema_required": True,
        "evidence_reference_required": True,
        "provider": {
            "base_url": "",
            "api_key": "",
            "api_key_env": "LLM_API_KEY",
            "api": "openai-completions",
            "model": "qianwen-3.5-plus",
            "timeout_seconds": 30,
            "endpoint_path": "/chat/completions"
        }
    },
    "risk_gate": {
        "min_confidence_for_auto_close": 0.75,
        "force_manual_review_levels": ["high", "critical"],
        "force_manual_review_when_pdf_low_conf": True,
    },
}


def load_policy(policy_path: str | None) -> Dict[str, Any]:
    if not policy_path:
        return DEFAULT_POLICY.copy()

    with open(policy_path, "r", encoding="utf-8") as f:
        user_policy = json.load(f)

    merged = _deep_merge(DEFAULT_POLICY, user_policy)
    validate_policy(merged)
    return merged


def validate_policy(policy: Dict[str, Any]) -> None:
    input_policy = policy.get("input_policy", {})
    llm_policy = policy.get("llm_policy", {})

    pdf_mode = input_policy.get("pdf_mode", "optional")
    if pdf_mode not in {"optional", "required", "disabled"}:
        raise ValueError("input_policy.pdf_mode must be optional|required|disabled")

    prefer_source = input_policy.get("prefer_source", "dwg")
    if prefer_source not in {"dwg", "pdf", "merge"}:
        raise ValueError("input_policy.prefer_source must be dwg|pdf|merge")

    dwg_cfg = input_policy.get("dwg", {})
    prefer_tool = dwg_cfg.get("prefer_tool", "auto")
    if prefer_tool not in {"auto", "ogr2ogr", "dwgread"}:
        raise ValueError("input_policy.dwg.prefer_tool must be auto|ogr2ogr|dwgread")

    tool_timeout_sec = dwg_cfg.get("tool_timeout_sec", 300)
    if not isinstance(tool_timeout_sec, int) or tool_timeout_sec <= 0:
        raise ValueError("input_policy.dwg.tool_timeout_sec must be a positive integer")

    default_coverage = dwg_cfg.get("default_coverage", 0.8)
    if not isinstance(default_coverage, (int, float)) or not (0 <= float(default_coverage) <= 1):
        raise ValueError("input_policy.dwg.default_coverage must be between 0 and 1")

    registration_cfg = dwg_cfg.get("registration", {})
    registration_mode = registration_cfg.get("mode", "auto")
    if registration_mode not in {"off", "auto", "manual"}:
        raise ValueError("input_policy.dwg.registration.mode must be off|auto|manual")

    auto_scale_candidates = registration_cfg.get("auto_scale_candidates", [1.0])
    if (
        not isinstance(auto_scale_candidates, list)
        or not auto_scale_candidates
        or not all(isinstance(x, (int, float)) and float(x) > 0 for x in auto_scale_candidates)
    ):
        raise ValueError("input_policy.dwg.registration.auto_scale_candidates must be a non-empty list of positive numbers")

    for key in ("min_overlap_ratio_to_skip", "min_improve_ratio"):
        val = registration_cfg.get(key, 0.0)
        if not isinstance(val, (int, float)) or float(val) < 0:
            raise ValueError(f"input_policy.dwg.registration.{key} must be a non-negative number")

    manual_transform = registration_cfg.get("manual_transform", {})
    for key in ("scale_x", "scale_y", "dx", "dy"):
        val = manual_transform.get(key, 0.0)
        if not isinstance(val, (int, float)):
            raise ValueError(f"input_policy.dwg.registration.manual_transform.{key} must be a number")
    if float(manual_transform.get("scale_x", 1.0)) <= 0 or float(manual_transform.get("scale_y", 1.0)) <= 0:
        raise ValueError("input_policy.dwg.registration.manual_transform scale_x/scale_y must be > 0")

    layer_mapping_cfg = dwg_cfg.get("layer_mapping", {})
    if not isinstance(layer_mapping_cfg.get("enabled", True), bool):
        raise ValueError("input_policy.dwg.layer_mapping.enabled must be a boolean")

    ignore_layers = layer_mapping_cfg.get("ignore_layers", [])
    if not isinstance(ignore_layers, list) or not all(isinstance(x, str) for x in ignore_layers):
        raise ValueError("input_policy.dwg.layer_mapping.ignore_layers must be a list of strings")

    unknown_type = layer_mapping_cfg.get("unknown_type", "unknown")
    if not isinstance(unknown_type, str) or not unknown_type.strip():
        raise ValueError("input_policy.dwg.layer_mapping.unknown_type must be a non-empty string")

    rules = layer_mapping_cfg.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("input_policy.dwg.layer_mapping.rules must be a list")
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"input_policy.dwg.layer_mapping.rules[{idx}] must be an object")
        match_mode = rule.get("match")
        if match_mode not in {"exact", "contains", "regex"}:
            raise ValueError(f"input_policy.dwg.layer_mapping.rules[{idx}].match must be exact|contains|regex")
        if not isinstance(rule.get("type"), str) or not rule.get("type", "").strip():
            raise ValueError(f"input_policy.dwg.layer_mapping.rules[{idx}].type must be a non-empty string")
        if match_mode == "regex":
            pattern = rule.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"input_policy.dwg.layer_mapping.rules[{idx}].pattern must be a non-empty string")
        else:
            key = rule.get("value")
            if not isinstance(key, str) or not key:
                raise ValueError(f"input_policy.dwg.layer_mapping.rules[{idx}].value must be a non-empty string")

    for key in ("dwgread_candidates", "ogr2ogr_candidates"):
        val = dwg_cfg.get(key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise ValueError(f"input_policy.dwg.{key} must be a list of strings")

    review_mode = llm_policy.get("review_mode", "off")
    if review_mode not in {"off", "selective", "always"}:
        raise ValueError("llm_policy.review_mode must be off|selective|always")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
