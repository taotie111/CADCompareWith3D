import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dwg_geometry_extractor import DWGGeometryExtractor


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _drift(cur: int, base: int) -> float:
    return abs(cur - base) / max(1, base)


def _percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * p
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(sorted_values[lo])
    weight = rank - lo
    return float(sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight)


def _normalize(path: str) -> str:
    return str(Path(path)).replace("\\", "/")


def _collect_files(baseline_json: str, dwg_dir: str | None, glob_pattern: str) -> List[str]:
    if dwg_dir:
        return [_normalize(str(p)) for p in sorted(Path(dwg_dir).glob(glob_pattern))]

    baseline = _load_json(baseline_json)
    files = [x.get("file") for x in baseline.get("details", []) if x.get("file")]
    return [_normalize(f) for f in files]


def _find_non_null_geometry_count(file_path: str, prefer_tool: str, timeout_sec: int, extractor: DWGGeometryExtractor) -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="dwg_regression_") as td:
        geojson_path = os.path.join(td, "tmp.geojson")

        chosen = None
        if prefer_tool == "ogr2ogr":
            ogr = extractor._find_ogr2ogr()
            if ogr:
                extractor._convert_with_ogr2ogr(ogr, file_path, geojson_path)
                chosen = "ogr2ogr"
        elif prefer_tool == "dwgread":
            dwgread = extractor._find_dwgread()
            if dwgread:
                extractor._convert_with_dwgread(dwgread, file_path, geojson_path)
                chosen = "dwgread"

        if not chosen:
            extractor._convert_dwg_to_geojson(file_path, geojson_path)

        data = _load_json(geojson_path)
        feats = data.get("features", []) if isinstance(data, dict) else []
        return sum(1 for x in feats if x.get("geometry"))


def main() -> None:
    parser = argparse.ArgumentParser(description="12个DWG批量回归验证")
    parser.add_argument("--baseline-json", default="checkpoints/dwg_validate/real_dwg_read_verify_summary.json")
    parser.add_argument("--output-json", default="checkpoints/dwg_validate/regression_latest.json")
    parser.add_argument("--dwg-dir", default=None, help="可选：直接从目录收集DWG")
    parser.add_argument("--glob", default="*.dwg")
    parser.add_argument("--grid-size", type=float, default=10.0)
    parser.add_argument("--max-feature-drift-ratio", type=float, default=0.02)
    parser.add_argument("--max-non-null-drift-ratio", type=float, default=0.02)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-elapsed-p95-sec", type=float, default=None, help="可选：P95耗时门禁（秒）")
    parser.add_argument("--max-elapsed-p50-sec", type=float, default=None, help="可选：P50耗时门禁（秒）")
    parser.add_argument("--libredwg-dir", default=None)
    parser.add_argument("--dwgread-path", default=None)
    parser.add_argument("--ogr2ogr-path", default=None)
    parser.add_argument("--tool-timeout-sec", type=int, default=None)
    parser.add_argument("--prefer-tool", choices=["auto", "ogr2ogr", "dwgread"], default="auto")
    parser.add_argument("--default-coverage", type=float, default=0.8)
    args = parser.parse_args()

    baseline = _load_json(args.baseline_json)
    baseline_map = {_normalize(x.get("file")): x for x in baseline.get("details", []) if x.get("file")}

    files = _collect_files(args.baseline_json, args.dwg_dir, args.glob)
    if not files:
        raise RuntimeError("未找到可回归的DWG文件")

    timeout_sec = args.tool_timeout_sec
    if timeout_sec is None:
        env_timeout = os.getenv("DWG_TOOL_TIMEOUT_SEC")
        try:
            timeout_sec = int(env_timeout) if env_timeout else 300
        except ValueError:
            timeout_sec = 300

    extractor = DWGGeometryExtractor(
        grid_size=args.grid_size,
        libredwg_dir=args.libredwg_dir,
        dwgread_path=args.dwgread_path,
        ogr2ogr_path=args.ogr2ogr_path,
        tool_timeout_sec=timeout_sec,
        prefer_tool=args.prefer_tool,
        default_coverage=args.default_coverage,
    )

    t0 = time.perf_counter()
    details: List[Dict[str, Any]] = []

    for file_path in files:
        rec: Dict[str, Any] = {
            "file": file_path,
            "ok": False,
            "elapsed_sec": 0.0,
            "features": 0,
            "non_null_geometry": 0,
            "baseline_features": None,
            "baseline_non_null_geometry": None,
            "feature_drift_ratio": None,
            "non_null_drift_ratio": None,
            "pass": False,
            "error": None,
        }
        st = time.perf_counter()
        try:
            if not Path(file_path).exists():
                raise FileNotFoundError(f"DWG 文件不存在: {file_path}")

            grid = extractor.extract_from_dwg(file_path)
            rec["ok"] = True
            rec["features"] = int(grid.get("total_features", 0))
            rec["non_null_geometry"] = _find_non_null_geometry_count(
                file_path=file_path,
                prefer_tool=args.prefer_tool,
                timeout_sec=timeout_sec,
                extractor=extractor,
            )
        except Exception as e:
            rec["error"] = str(e)
        rec["elapsed_sec"] = round(time.perf_counter() - st, 3)

        base = baseline_map.get(file_path)
        if base:
            b_feat = int(base.get("features", 0))
            b_non_null = int(base.get("non_null_geometry", 0))
            rec["baseline_features"] = b_feat
            rec["baseline_non_null_geometry"] = b_non_null
            rec["feature_drift_ratio"] = round(_drift(rec["features"], b_feat), 6)
            rec["non_null_drift_ratio"] = round(_drift(rec["non_null_geometry"], b_non_null), 6)
        else:
            rec["feature_drift_ratio"] = 0.0
            rec["non_null_drift_ratio"] = 0.0

        rec["pass"] = bool(
            rec["ok"]
            and rec["feature_drift_ratio"] <= args.max_feature_drift_ratio
            and rec["non_null_drift_ratio"] <= args.max_non_null_drift_ratio
        )
        details.append(rec)

    total = len(details)
    ok_files = sum(1 for x in details if x["ok"])
    failed_files = total - ok_files
    pass_files = sum(1 for x in details if x["pass"])
    pass_rate = pass_files / total if total else 0.0

    elapsed_values = sorted([float(x.get("elapsed_sec", 0.0) or 0.0) for x in details])
    elapsed_stats = {
        "min": round(elapsed_values[0], 3) if elapsed_values else 0.0,
        "max": round(elapsed_values[-1], 3) if elapsed_values else 0.0,
        "mean": round(float(statistics.mean(elapsed_values)), 3) if elapsed_values else 0.0,
        "median": round(float(statistics.median(elapsed_values)), 3) if elapsed_values else 0.0,
        "p50": round(_percentile(elapsed_values, 0.50), 3),
        "p95": round(_percentile(elapsed_values, 0.95), 3),
    }

    perf_gate = {
        "max_elapsed_p50_sec": args.max_elapsed_p50_sec,
        "max_elapsed_p95_sec": args.max_elapsed_p95_sec,
        "p50_pass": True,
        "p95_pass": True,
    }
    if args.max_elapsed_p50_sec is not None:
        perf_gate["p50_pass"] = elapsed_stats["p50"] <= float(args.max_elapsed_p50_sec)
    if args.max_elapsed_p95_sec is not None:
        perf_gate["p95_pass"] = elapsed_stats["p95"] <= float(args.max_elapsed_p95_sec)

    overall_pass = bool(
        failed_files == 0
        and pass_rate >= args.min_pass_rate
        and perf_gate["p50_pass"]
        and perf_gate["p95_pass"]
    )

    result = {
        "run_id": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_files": total,
        "ok_files": ok_files,
        "failed_files": failed_files,
        "pass_rate": round(pass_rate, 6),
        "elapsed_total_sec": round(time.perf_counter() - t0, 3),
        "thresholds": {
            "max_feature_drift_ratio": args.max_feature_drift_ratio,
            "max_non_null_drift_ratio": args.max_non_null_drift_ratio,
            "min_pass_rate": args.min_pass_rate,
        },
        "performance_gate": perf_gate,
        "elapsed_stats_sec": elapsed_stats,
        "drift_summary": {
            "feature_drift_exceeded": sum(
                1 for x in details if (x["feature_drift_ratio"] or 0) > args.max_feature_drift_ratio
            ),
            "non_null_drift_exceeded": sum(
                1 for x in details if (x["non_null_drift_ratio"] or 0) > args.max_non_null_drift_ratio
            ),
        },
        "details": details,
        "overall_pass": overall_pass,
    }

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": _normalize(str(out_path)),
                "total_files": total,
                "ok_files": ok_files,
                "failed_files": failed_files,
                "overall_pass": result["overall_pass"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
