"""
run_grid_compare.py
===================

网格对比流程的 CLI 入口，整合方案 A 的完整流程。

流程：
  1. 解析 tileset.json（实景端）
  2. 网格化实景端 → grid_reality.json
  3. 解析设计端（CSV 或 DWG） → grid_design.json
  4. 执行网格对比 → grid_compare_result.json

使用示例：
    python run_grid_compare.py \
        --tileset https://example.com/tileset.json \
        --design design_annotations.csv \
        --output ./results/

    # 使用自定义参数
    python run_grid_compare.py \
        --tileset ./tileset.json \
        --design ./design.csv \
        --grid-size 5.0 \
        --threshold 0.2 \
        --max-depth 3

输出文件：
  - {output}/grid_reality.json    # 实景端网格
  - {output}/grid_design.json     # 设计端网格
  - {output}/grid_compare_result.json  # 对比结果（含事件列表）
"""

import argparse
import copy
import json
import math
import os
import sys
import time
from typing import Any, Dict, List

from grid_builder import GridBuilder
from grid_compare import GridCompare
from dwg_geometry_extractor import DWGGeometryExtractor
from policy_loader import load_policy


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cell_id_from_xy(x: float, y: float, x_origin: float, y_origin: float, grid_size: float) -> str:
    col = int(math.floor((x - x_origin) / grid_size))
    row = int(math.floor((y - y_origin) / grid_size))
    return f"R{row}_C{col}"


def _bbox_from_cells(cells: List[Dict[str, Any]], grid_size: float) -> Dict[str, float] | None:
    if not cells:
        return None

    x_min = float("inf")
    y_min = float("inf")
    x_max = float("-inf")
    y_max = float("-inf")

    for cell in cells:
        cx_min = _safe_float(cell.get("x_min"), 0.0)
        cy_min = _safe_float(cell.get("y_min"), 0.0)
        cx_max = _safe_float(cell.get("x_max"), cx_min + grid_size)
        cy_max = _safe_float(cell.get("y_max"), cy_min + grid_size)
        x_min = min(x_min, cx_min)
        y_min = min(y_min, cy_min)
        x_max = max(x_max, cx_max)
        y_max = max(y_max, cy_max)

    if x_min == float("inf"):
        return None

    return {
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
        "width": max(0.0, x_max - x_min),
        "height": max(0.0, y_max - y_min),
    }


def _bbox_overlap_ratio(a: Dict[str, float] | None, b: Dict[str, float] | None) -> float:
    if not a or not b:
        return 0.0

    inter_x_min = max(a["x_min"], b["x_min"])
    inter_y_min = max(a["y_min"], b["y_min"])
    inter_x_max = min(a["x_max"], b["x_max"])
    inter_y_max = min(a["y_max"], b["y_max"])
    inter_area = max(0.0, inter_x_max - inter_x_min) * max(0.0, inter_y_max - inter_y_min)
    area_a = max(0.0, a["width"] * a["height"])
    area_b = max(0.0, b["width"] * b["height"])
    denom = max(1e-9, min(area_a, area_b))
    return inter_area / denom


def _transform_bbox(bbox: Dict[str, float] | None, transform: Dict[str, float]) -> Dict[str, float] | None:
    if not bbox:
        return None

    sx = _safe_float(transform.get("scale_x"), 1.0)
    sy = _safe_float(transform.get("scale_y"), 1.0)
    dx = _safe_float(transform.get("dx"), 0.0)
    dy = _safe_float(transform.get("dy"), 0.0)

    tx_min = sx * bbox["x_min"] + dx
    tx_max = sx * bbox["x_max"] + dx
    ty_min = sy * bbox["y_min"] + dy
    ty_max = sy * bbox["y_max"] + dy

    x_min, x_max = min(tx_min, tx_max), max(tx_min, tx_max)
    y_min, y_max = min(ty_min, ty_max), max(ty_min, ty_max)

    return {
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
        "width": max(0.0, x_max - x_min),
        "height": max(0.0, y_max - y_min),
    }


def _remap_design_cells(
    design_cells: List[Dict[str, Any]],
    transform: Dict[str, float],
    reality_x_origin: float,
    reality_y_origin: float,
    grid_size: float,
) -> List[Dict[str, Any]]:
    sx = _safe_float(transform.get("scale_x"), 1.0)
    sy = _safe_float(transform.get("scale_y"), 1.0)
    dx = _safe_float(transform.get("dx"), 0.0)
    dy = _safe_float(transform.get("dy"), 0.0)

    remapped: Dict[str, Dict[str, Any]] = {}
    for cell in design_cells:
        x_min = _safe_float(cell.get("x_min"), 0.0)
        y_min = _safe_float(cell.get("y_min"), 0.0)
        x_max = _safe_float(cell.get("x_max"), x_min + grid_size)
        y_max = _safe_float(cell.get("y_max"), y_min + grid_size)

        cx = 0.5 * (x_min + x_max)
        cy = 0.5 * (y_min + y_max)
        tx = sx * cx + dx
        ty = sy * cy + dy

        cell_id = _cell_id_from_xy(tx, ty, reality_x_origin, reality_y_origin, grid_size)
        row_col = cell_id.split("_")
        row = int(row_col[0][1:])
        col = int(row_col[1][1:])
        nx_min = reality_x_origin + col * grid_size
        ny_min = reality_y_origin + row * grid_size

        coverage = _safe_float(cell.get("coverage", cell.get("design_coverage", 0.0)), 0.0)
        features = cell.get("features") or []

        if cell_id not in remapped:
            remapped[cell_id] = {
                "cell_id": cell_id,
                "x_min": nx_min,
                "y_min": ny_min,
                "x_max": nx_min + grid_size,
                "y_max": ny_min + grid_size,
                "sum_coverage": 0.0,
                "count": 0,
                "features": set(),
            }

        slot = remapped[cell_id]
        slot["sum_coverage"] += coverage
        slot["count"] += 1
        for f in features:
            slot["features"].add(str(f))

    out: List[Dict[str, Any]] = []
    for slot in remapped.values():
        avg_coverage = slot["sum_coverage"] / max(1, slot["count"])
        out.append(
            {
                "cell_id": slot["cell_id"],
                "x_min": round(slot["x_min"], 3),
                "y_min": round(slot["y_min"], 3),
                "x_max": round(slot["x_max"], 3),
                "y_max": round(slot["y_max"], 3),
                "coverage": round(avg_coverage, 4),
                "design_coverage": round(avg_coverage, 4),
                "features": sorted(slot["features"]),
            }
        )

    out.sort(key=lambda item: item["cell_id"])
    return out


def _cell_overlap_ratio(design_cells: List[Dict[str, Any]], reality_cell_ids: set[str]) -> float:
    design_ids = {str(c.get("cell_id")) for c in design_cells if c.get("cell_id") is not None}
    if not design_ids or not reality_cell_ids:
        return 0.0
    inter = len(design_ids & reality_cell_ids)
    denom = max(1, min(len(design_ids), len(reality_cell_ids)))
    return inter / denom


def _apply_registration(
    grid_design: Dict[str, Any],
    grid_reality: Any,
    registration_cfg: Dict[str, Any],
    grid_size: float,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    design_cells = list(grid_design.get("cells", []))
    reality_cells = [c.to_dict() for c in grid_reality.cells]
    reality_cell_ids = {c.cell_id for c in grid_reality.cells}
    rx0 = _safe_float(getattr(grid_reality, "x_origin", 0.0))
    ry0 = _safe_float(getattr(grid_reality, "y_origin", 0.0))

    identity = {"scale_x": 1.0, "scale_y": 1.0, "dx": 0.0, "dy": 0.0}
    mode = str(registration_cfg.get("mode", "auto"))

    base_remapped = _remap_design_cells(design_cells, identity, rx0, ry0, grid_size)
    base_overlap_ratio = _cell_overlap_ratio(base_remapped, reality_cell_ids)

    report: Dict[str, Any] = {
        "mode": mode,
        "attempted": mode != "off",
        "applied": False,
        "reason": "off",
        "transform": identity,
        "before_overlap_ratio": base_overlap_ratio,
        "after_overlap_ratio": base_overlap_ratio,
        "before_bbox_overlap_ratio": 0.0,
        "after_bbox_overlap_ratio": 0.0,
        "candidate_count": 0,
    }

    design_bbox = _bbox_from_cells(design_cells, grid_size)
    reality_bbox = _bbox_from_cells(reality_cells, grid_size)
    report["before_bbox_overlap_ratio"] = _bbox_overlap_ratio(design_bbox, reality_bbox)
    report["after_bbox_overlap_ratio"] = report["before_bbox_overlap_ratio"]

    if mode == "off":
        report["reason"] = "registration_disabled"
        return grid_design, report

    if not design_cells or not reality_cells or not design_bbox or not reality_bbox:
        report["reason"] = "insufficient_cells_for_registration"
        return grid_design, report

    min_overlap_ratio_to_skip = _safe_float(registration_cfg.get("min_overlap_ratio_to_skip"), 0.05)
    min_improve_ratio = _safe_float(registration_cfg.get("min_improve_ratio"), 0.2)

    if report["before_overlap_ratio"] >= min_overlap_ratio_to_skip:
        report["reason"] = "skip_already_overlapped"
        out_grid = copy.deepcopy(grid_design)
        out_grid["cells"] = base_remapped
        out_grid["cell_count"] = len(base_remapped)
        out_grid["x_origin"] = round(rx0, 3)
        out_grid["y_origin"] = round(ry0, 3)
        return out_grid, report

    candidate_transforms: List[Dict[str, float]] = []
    if mode == "manual":
        mt = registration_cfg.get("manual_transform", {})
        candidate_transforms.append(
            {
                "scale_x": _safe_float(mt.get("scale_x"), 1.0),
                "scale_y": _safe_float(mt.get("scale_y"), 1.0),
                "dx": _safe_float(mt.get("dx"), 0.0),
                "dy": _safe_float(mt.get("dy"), 0.0),
            }
        )
    else:
        scales = registration_cfg.get("auto_scale_candidates", [1.0]) or [1.0]
        dw = max(1e-9, design_bbox["width"])
        dh = max(1e-9, design_bbox["height"])
        rw = max(1e-9, reality_bbox["width"])
        rh = max(1e-9, reality_bbox["height"])
        base_sx = rw / dw
        base_sy = rh / dh

        d_cx = 0.5 * (design_bbox["x_min"] + design_bbox["x_max"])
        d_cy = 0.5 * (design_bbox["y_min"] + design_bbox["y_max"])
        r_cx = 0.5 * (reality_bbox["x_min"] + reality_bbox["x_max"])
        r_cy = 0.5 * (reality_bbox["y_min"] + reality_bbox["y_max"])

        for raw in scales:
            s = _safe_float(raw, 1.0)
            if s <= 0:
                continue

            for sx, sy in ((base_sx * s, base_sy * s), (s, s)):
                if sx <= 0 or sy <= 0:
                    continue

                candidate_transforms.append(
                    {
                        "scale_x": sx,
                        "scale_y": sy,
                        "dx": reality_bbox["x_min"] - sx * design_bbox["x_min"],
                        "dy": reality_bbox["y_min"] - sy * design_bbox["y_min"],
                    }
                )
                candidate_transforms.append(
                    {
                        "scale_x": sx,
                        "scale_y": sy,
                        "dx": r_cx - sx * d_cx,
                        "dy": r_cy - sy * d_cy,
                    }
                )

    if not candidate_transforms:
        report["reason"] = "no_valid_registration_candidate"
        return grid_design, report

    report["candidate_count"] = len(candidate_transforms)

    best_score = -1.0
    best_bbox_score = -1.0
    best_cells = design_cells
    best_transform = identity

    for transform in candidate_transforms:
        remapped = _remap_design_cells(design_cells, transform, rx0, ry0, grid_size)
        score = _cell_overlap_ratio(remapped, reality_cell_ids)
        bbox_score = _bbox_overlap_ratio(_transform_bbox(design_bbox, transform), reality_bbox)
        if score > best_score or (abs(score - best_score) < 1e-9 and bbox_score > best_bbox_score):
            best_score = score
            best_bbox_score = bbox_score
            best_cells = remapped
            best_transform = transform

    improvement = best_score - report["before_overlap_ratio"]
    report["after_overlap_ratio"] = max(0.0, best_score)
    report["after_bbox_overlap_ratio"] = max(0.0, best_bbox_score)
    report["transform"] = {
        "scale_x": round(_safe_float(best_transform.get("scale_x"), 1.0), 8),
        "scale_y": round(_safe_float(best_transform.get("scale_y"), 1.0), 8),
        "dx": round(_safe_float(best_transform.get("dx"), 0.0), 6),
        "dy": round(_safe_float(best_transform.get("dy"), 0.0), 6),
    }

    if mode == "manual":
        report["applied"] = True
        report["reason"] = "manual_registration_applied"
    else:
        required_improve = min_improve_ratio
        if report["before_overlap_ratio"] <= 1e-9:
            required_improve = min(min_improve_ratio, 0.01)
        if best_score <= report["before_overlap_ratio"] or improvement < required_improve:
            report["reason"] = "auto_registration_improve_not_enough"
            out_grid = copy.deepcopy(grid_design)
            out_grid["cells"] = base_remapped
            out_grid["cell_count"] = len(base_remapped)
            out_grid["x_origin"] = round(rx0, 3)
            out_grid["y_origin"] = round(ry0, 3)
            return out_grid, report
        report["applied"] = True
        report["reason"] = "auto_registration_applied"

    out_grid = copy.deepcopy(grid_design)
    out_grid["cells"] = best_cells
    out_grid["cell_count"] = len(best_cells)
    out_grid["x_origin"] = round(rx0, 3)
    out_grid["y_origin"] = round(ry0, 3)
    return out_grid, report


def run(
    tileset_source: str,
    design_source: str,
    output_dir: str = ".",
    grid_size: float = 10.0,
    deviation_threshold: float = 0.3,
    max_depth: int = None,
    use_cache: bool = False,
    cache_dir: str = None,
    policy_path: str = None,
    libredwg_dir: str = None,
    dwgread_path: str = None,
    ogr2ogr_path: str = None,
    tool_timeout_sec: int = None,
    prefer_tool: str = None,
    default_coverage: float = None,
    dwgread_candidates: list[str] = None,
    ogr2ogr_candidates: list[str] = None,
    baseline_report_path: str = None,
) -> dict:
    """
    执行网格对比流程。

    Args:
        tileset_source: tileset.json URL 或本地路径
        design_source: 设计端数据（CSV 标注文件或 DWG 文件路径）
        output_dir: 输出目录
        grid_size: 网格边长（米）
        deviation_threshold: 偏差阈值
        max_depth: 最大 Tile 深度
        use_cache: 是否使用缓存
        cache_dir: 缓存目录路径

    Returns:
        对比结果字典
    """
    os.makedirs(output_dir, exist_ok=True)
    t0 = time.perf_counter()

    policy = load_policy(policy_path)
    dwg_policy = policy.get("input_policy", {}).get("dwg", {})

    if tool_timeout_sec is not None:
        effective_tool_timeout = int(tool_timeout_sec)
    else:
        raw_timeout = os.getenv("DWG_TOOL_TIMEOUT_SEC", str(dwg_policy.get("tool_timeout_sec", 300)))
        try:
            effective_tool_timeout = int(raw_timeout)
        except (TypeError, ValueError):
            effective_tool_timeout = 300

    effective_prefer_tool = prefer_tool or dwg_policy.get("prefer_tool", "auto")
    effective_default_coverage = (
        default_coverage if default_coverage is not None else dwg_policy.get("default_coverage", 0.8)
    )
    effective_dwgread_candidates = (
        dwgread_candidates if dwgread_candidates is not None else dwg_policy.get("dwgread_candidates", [])
    )
    effective_ogr2ogr_candidates = (
        ogr2ogr_candidates if ogr2ogr_candidates is not None else dwg_policy.get("ogr2ogr_candidates", [])
    )
    registration_cfg = dwg_policy.get("registration", {}) or {}
    layer_mapping_cfg = dwg_policy.get("layer_mapping", {}) or {}

    print("=" * 60)
    print("  网格对比引擎 - 方案 A")
    print("=" * 60)

    # Step 1: 解析 tileset → 网格化实景端
    s1 = time.perf_counter()
    print("\n[1/4] 解析 tileset.json ...")
    from tileset_parser import TilesetParser

    parser = TilesetParser(cache_dir=cache_dir or output_dir)
    if tileset_source.startswith("http://") or tileset_source.startswith("https://"):
        tileset = parser.load_from_url(tileset_source, use_cache=use_cache)
    else:
        tileset = parser.load_from_file(tileset_source)

    print(f"  tileset 总 Tile 数: {tileset.total_tiles}")
    print(f"  坐标系统: {tileset.coordinate_system_note}")
    t_step1 = round(time.perf_counter() - s1, 3)

    # Step 2: 网格化实景端
    s2 = time.perf_counter()
    print("\n[2/4] 网格化实景端（3D Tiles → Grid）...")
    builder = GridBuilder(grid_size=grid_size)
    grid_reality = builder.build_from_tileset(tileset, max_depth=max_depth)

    grid_reality_path = os.path.join(output_dir, "grid_reality.json")
    builder.save_json(grid_reality, grid_reality_path)
    print(f"  实景网格: {len(grid_reality.cells)} 个格子，{grid_reality.total_tiles} 个 Tile")
    print(f"  已保存 -> {grid_reality_path}")
    t_step2 = round(time.perf_counter() - s2, 3)

    # Step 3: 解析设计端
    s3 = time.perf_counter()
    print("\n[3/4] 解析设计端数据（CAD → Grid）...")
    extractor = DWGGeometryExtractor(
        grid_size=grid_size,
        libredwg_dir=libredwg_dir,
        dwgread_path=dwgread_path,
        ogr2ogr_path=ogr2ogr_path,
        tool_timeout_sec=effective_tool_timeout,
        prefer_tool=effective_prefer_tool,
        dwgread_candidates=effective_dwgread_candidates,
        ogr2ogr_candidates=effective_ogr2ogr_candidates,
        default_coverage=effective_default_coverage,
        layer_mapping=layer_mapping_cfg,
    )

    if design_source.lower().endswith(".csv"):
        grid_design = extractor.extract_from_csv(design_source)
    else:
        grid_design = extractor.extract_from_dwg(design_source)

    registered_grid_design, registration_report = _apply_registration(
        grid_design=grid_design,
        grid_reality=grid_reality,
        registration_cfg=registration_cfg,
        grid_size=grid_size,
    )
    grid_design = registered_grid_design

    grid_design_path = os.path.join(output_dir, "grid_design.json")
    extractor.save_json(grid_design, grid_design_path)
    print(f"  设计网格: {grid_design.get('total_features', 0)} 个构件，"
          f"{len(grid_design.get('cells', []))} 个格子")
    print(f"  已保存 -> {grid_design_path}")
    t_step3 = round(time.perf_counter() - s3, 3)

    # Step 4: 执行对比
    s4 = time.perf_counter()
    print("\n[4/4] 执行网格对比...")
    compare = GridCompare(grid_size=grid_size, deviation_threshold=deviation_threshold)
    result = compare.compare(
        grid_design_path=grid_design_path,
        grid_reality_path=grid_reality_path,
    )

    result_path = os.path.join(output_dir, "grid_compare_result.json")
    compare.save_json(result, result_path)
    compare.print_summary(result)
    print(f"\n  已保存 -> {result_path}")
    t_step4 = round(time.perf_counter() - s4, 3)

    result_dict = result.to_dict()

    if baseline_report_path:
        report = {
            "pipeline": "run_grid_compare",
            "inputs": {
                "tileset": tileset_source,
                "design": design_source,
            },
            "params": {
                "grid_size": grid_size,
                "threshold": deviation_threshold,
                "max_depth": max_depth,
                "cache": use_cache,
                "cache_dir": cache_dir,
                "tool_timeout_sec": effective_tool_timeout,
                "prefer_tool": effective_prefer_tool,
                "default_coverage": effective_default_coverage,
                "dwgread_path": dwgread_path,
                "ogr2ogr_path": ogr2ogr_path,
                "libredwg_dir": libredwg_dir,
                "registration_mode": registration_cfg.get("mode", "auto"),
            },
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "steps_elapsed_sec": {
                "load_tileset": t_step1,
                "build_reality_grid": t_step2,
                "build_design_grid": t_step3,
                "compare_grid": t_step4,
            },
            "grid_design": {
                "total_features": int(grid_design.get("total_features", 0)),
                "cell_count": int(grid_design.get("cell_count", len(grid_design.get("cells", [])))),
                "mapping_stats": grid_design.get("mapping_stats", {}),
            },
            "grid_reality": {
                "total_tiles": int(grid_reality.total_tiles),
                "cell_count": int(len(grid_reality.cells)),
            },
            "compare_summary": result_dict.get("summary", {}),
            "events_count": int(len(result_dict.get("events", []))),
            "registration": registration_report,
            "artifacts": {
                "grid_reality": grid_reality_path,
                "grid_design": grid_design_path,
                "grid_compare_result": result_path,
            },
        }
        with open(baseline_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  基线报告已保存 -> {baseline_report_path}")

    print("\n" + "=" * 60)
    print("  对比完成")
    print("=" * 60)

    return result_dict


def main() -> None:
    parser = argparse.ArgumentParser(
        description="网格对比 CLI（方案 A：CAD vs 3D Tiles 网格对比）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法（CSV 设计端标注）
  python run_grid_compare.py --tileset tileset.json --design design.csv

  # 使用远程 tileset + 自定义参数
  python run_grid_compare.py \\
      --tileset https://example.com/tileset.json \\
      --design design.csv \\
      --grid-size 5 \\
      --threshold 0.2 \\
      --output ./output/

  # 使用本地 tileset + DWG 设计端
  python run_grid_compare.py --tileset ./tileset.json --design design.dwg
        """,
    )
    parser.add_argument("--tileset", required=True, help="tileset.json URL 或本地路径")
    parser.add_argument("--design", required=True, help="设计端数据（CSV 标注 或 DWG 文件）")
    parser.add_argument("--output", default=".", help="输出目录（默认当前目录）")
    parser.add_argument("--grid-size", type=float, default=10.0, help="网格边长（米，默认 10）")
    parser.add_argument("--threshold", type=float, default=0.3, help="偏差阈值（默认 0.3）")
    parser.add_argument("--max-depth", type=int, default=None, help="最大 Tile 深度（默认不限）")
    parser.add_argument("--cache", action="store_true", help="启用缓存（减少重复下载）")
    parser.add_argument("--cache-dir", default=None, help="缓存目录路径")
    parser.add_argument("--policy", default=None, help="策略配置文件路径（JSON）")
    parser.add_argument("--libredwg-dir", default=None, help="libredwg_win64 目录路径")
    parser.add_argument("--dwgread-path", default=None, help="dwgread 可执行文件路径")
    parser.add_argument("--ogr2ogr-path", default=None, help="ogr2ogr 可执行文件路径")
    parser.add_argument("--tool-timeout-sec", type=int, default=None, help="DWG 工具转换超时（秒）")
    parser.add_argument("--prefer-tool", choices=["auto", "ogr2ogr", "dwgread"], default=None, help="DWG 转换工具优先级")
    parser.add_argument("--default-coverage", type=float, default=None, help="DWG 特征默认覆盖得分（0~1）")
    parser.add_argument("--baseline-report", default=None, help="保存结构化基线报告 JSON 路径")

    args = parser.parse_args()

    try:
        run(
            tileset_source=args.tileset,
            design_source=args.design,
            output_dir=args.output,
            grid_size=args.grid_size,
            deviation_threshold=args.threshold,
            max_depth=args.max_depth,
            use_cache=args.cache,
            cache_dir=args.cache_dir,
            policy_path=args.policy,
            libredwg_dir=args.libredwg_dir,
            dwgread_path=args.dwgread_path,
            ogr2ogr_path=args.ogr2ogr_path,
            tool_timeout_sec=args.tool_timeout_sec,
            prefer_tool=args.prefer_tool,
            default_coverage=args.default_coverage,
            baseline_report_path=args.baseline_report,
        )
    except Exception as exc:
        print(f"\n错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()