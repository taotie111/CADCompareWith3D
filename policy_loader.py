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
            "model": "qwen3.5-plus",
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
