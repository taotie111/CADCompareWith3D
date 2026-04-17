import argparse

from compare_engine import load_json, save_json, run_diff_and_risk
from input_normalizer import normalize_design_input, normalize_reality_input
from llm_reviewer import LLMReviewer
from policy_loader import load_policy


def main() -> None:
    parser = argparse.ArgumentParser(description="水利工程 CAD vs 实景 对比引擎（增强版）")
    parser.add_argument("--design", required=True, help="设计数据JSON路径")
    parser.add_argument("--reality", required=True, help="实景数据JSON路径")
    parser.add_argument("--rules", required=True, help="规则配置JSON路径")
    parser.add_argument("--out", required=True, help="输出结果JSON路径")
    parser.add_argument("--policy", required=False, help="策略配置JSON路径")
    parser.add_argument("--llm", action="store_true", help="启用LLM复核")
    args = parser.parse_args()

    design_data = load_json(args.design)
    reality_data = load_json(args.reality)
    ruleset = load_json(args.rules)
    policy = load_policy(args.policy)

    normalized_design = normalize_design_input(design_data, policy["input_policy"])
    normalized_reality = normalize_reality_input(reality_data)

    llm_policy = dict(policy.get("llm_policy", {}))
    if args.llm:
        llm_policy["enabled"] = True
        if llm_policy.get("review_mode", "off") == "off":
            llm_policy["review_mode"] = "selective"

    reviewer = LLMReviewer(
        enabled=bool(llm_policy.get("enabled", False)),
        provider=llm_policy.get("provider", {}),
    )

    result = run_diff_and_risk(
        normalized_design,
        normalized_reality,
        ruleset,
        input_policy=policy.get("input_policy", {}),
        llm_policy=llm_policy,
        risk_gate=policy.get("risk_gate", {}),
        llm_reviewer=reviewer,
    )
    save_json(args.out, result)

    s = result["summary"]
    print("=== Compare Engine Result ===")
    print(f"Matched: {s['matched_objects']}")
    print(f"Missing: {s['missing_objects']}")
    print(f"Unplanned: {s['unplanned_objects']}")
    print(f"Events total: {s['events_total']}")
    print(f"Events by level: {s['events_by_level']}")
    print(f"Manual review required: {s['manual_review_required']}")
    print(f"LLM reviews: {s['llm_reviews_total']}")
    print(f"Output: {args.out}")


if __name__ == "__main__":
    main()
