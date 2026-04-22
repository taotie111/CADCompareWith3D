"""
dwg_geometry_extractor.py
=========================

从 DWG 文件或 CSV 标注文件提取设计端几何信息，输出设计端网格数据。

支持的输入：
  1. DWG 文件（需安装 LibreDWG 命令行工具 `ogr2ogr`）
  2. CSV 人工标注文件（降级方案，无需外部依赖）

CSV 格式（设计端人工标注）：
  type,x_min,y_min,x_max,y_max,coverage,elevation
  flood_wall,500000,3500010,500020,3500030,0.85,1852.5
  spillway,500030,3500040,500080,3500050,0.6,1854.0
  gate_chamber,500100,3500055,500150,3500070,0.9,1853.2

字段说明：
  - type: 构件类型（flood_wall | spillway | gate_chamber | tunnel | panel_dam | supply）
  - x_min/y_min/x_max/y_max: 构件外包矩形（米，CGCS2000 投影坐标）
  - coverage: 该区域的 CAD 覆盖比例（0~1）
  - elevation: 标高（米）

输出格式（grid_design.json）：
  {
    "source": "csv" | "dwg",
    "total_features": 42,
    "grid_size": 10.0,
    "cells": [
      {
        "cell_id": "R0_C1",
        "x_min": 500000.0,
        "y_min": 3500010.0,
        "x_max": 500010.0,
        "y_max": 3500020.0,
        "coverage": 0.85,         // 设计端覆盖得分
        "features": ["flood_wall", "spillway"]  // 落入该格子的构件类型
      }
    ]
  }

使用示例：
    extractor = DWGGeometryExtractor(grid_size=10.0)
    grid = extractor.extract_from_csv("design_annotations.csv")
    extractor.save_json(grid, "grid_design.json")

    # 或使用 DWG（需 ogr2ogr）：
    grid = extractor.extract_from_dwg("design.dwg")
"""

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class DesignFeature:
    """单个设计构件。"""
    type: str          # 构件类型
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    coverage: float    # 0~1
    elevation: float   # 米


@dataclass
class DesignGridCell:
    """设计端网格格子。"""
    cell_id: str
    x_min: float
    y_min: float
    coverage: float    # 聚合后的覆盖得分
    features: List[str]  # 落入的构件类型列表


class DWGGeometryExtractor:
    """
    设计端几何提取器。

    支持的输入模式：
      - CSV（优先，始终可用）
      - DWG（需系统安装 LibreDWG/ogr2ogr）
    """

    FEATURE_TYPES = {
        "flood_wall": "防渗墙",
        "spillway": "溢洪道",
        "gate_chamber": "闸室",
        "tunnel": "导流洞",
        "panel_dam": "面板坝",
        "supply": "供水设施",
    }

    def __init__(
        self,
        grid_size: float = 10.0,
        libredwg_dir: Optional[str] = None,
        dwgread_path: Optional[str] = None,
        ogr2ogr_path: Optional[str] = None,
        tool_timeout_sec: int = 300,
        prefer_tool: str = "auto",
        dwgread_candidates: Optional[List[str]] = None,
        ogr2ogr_candidates: Optional[List[str]] = None,
        default_coverage: float = 0.8,
        layer_mapping: Optional[Dict[str, Any]] = None,
    ):
        self.grid_size = grid_size
        self.libredwg_dir = libredwg_dir
        self.dwgread_path = dwgread_path
        self.ogr2ogr_path = ogr2ogr_path
        self.tool_timeout_sec = max(1, int(tool_timeout_sec))
        self.prefer_tool = prefer_tool if prefer_tool in {"auto", "ogr2ogr", "dwgread"} else "auto"
        self.dwgread_candidates = list(dwgread_candidates or [])
        self.ogr2ogr_candidates = list(ogr2ogr_candidates or [])
        self.default_coverage = float(default_coverage)
        if self.default_coverage < 0:
            self.default_coverage = 0.0
        if self.default_coverage > 1:
            self.default_coverage = 1.0

        layer_mapping = layer_mapping or {}
        self.layer_mapping_enabled = bool(layer_mapping.get("enabled", False))
        self.layer_mapping_unknown_type = str(layer_mapping.get("unknown_type", "unknown") or "unknown")
        self.layer_mapping_ignore_layers = {
            self._normalize_layer_name(x)
            for x in layer_mapping.get("ignore_layers", [])
            if isinstance(x, str) and x.strip()
        }
        self.layer_mapping_rules = self._compile_layer_mapping_rules(layer_mapping.get("rules", []))

    # -------------------------------------------------------------------------
    # CSV 模式（降级方案）
    # -------------------------------------------------------------------------

    def extract_from_csv(self, csv_path: str) -> Dict[str, Any]:
        """
        从 CSV 标注文件提取设计端网格。

        Args:
            csv_path: CSV 文件路径

        Returns:
            grid_design.json 兼容的字典
        """
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

        features = self._parse_csv(csv_path)
        return self._build_design_grid(features, source="csv")

    def _parse_csv(self, csv_path: str) -> List[DesignFeature]:
        """解析 CSV 文件为 DesignFeature 列表。"""
        features = []
        with open(csv_path, "r", encoding="utf-8") as f:
            header = None
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if header is None:
                    header = line.split(",")
                    continue
                fields = line.split(",")
                if len(fields) < 6:
                    continue
                try:
                    feat = DesignFeature(
                        type=fields[0].strip(),
                        x_min=float(fields[1]),
                        y_min=float(fields[2]),
                        x_max=float(fields[3]),
                        y_max=float(fields[4]),
                        coverage=float(fields[5]),
                        elevation=float(fields[6]) if len(fields) > 6 else 0.0,
                    )
                    features.append(feat)
                except ValueError:
                    continue
        return features

    def _build_design_grid(
        self,
        features: List[DesignFeature],
        source: str = "csv",
        mapping_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """将设计构件列表转换为网格数据。"""
        if not features:
            out = {
                "source": source,
                "total_features": 0,
                "grid_size": self.grid_size,
                "cells": [],
            }
            if mapping_stats is not None:
                out["mapping_stats"] = mapping_stats
            return out

        x_min = min(min(f.x_min, f.x_max) for f in features)
        y_min = min(min(f.y_min, f.y_max) for f in features)
        x_origin = math.floor(x_min / self.grid_size) * self.grid_size
        y_origin = math.floor(y_min / self.grid_size) * self.grid_size

        cell_map: Dict[str, Dict[str, Any]] = {}

        for feat in features:
            feat_x_min = min(feat.x_min, feat.x_max)
            feat_x_max = max(feat.x_min, feat.x_max)
            feat_y_min = min(feat.y_min, feat.y_max)
            feat_y_max = max(feat.y_min, feat.y_max)

            col_start = int((feat_x_min - x_origin) / self.grid_size)
            col_end = int((feat_x_max - x_origin) / self.grid_size)
            row_start = int((feat_y_min - y_origin) / self.grid_size)
            row_end = int((feat_y_max - y_origin) / self.grid_size)

            for row in range(row_start, row_end + 1):
                for col in range(col_start, col_end + 1):
                    cell_id = f"R{row}_C{col}"

                    x_min_cell = x_origin + col * self.grid_size
                    y_min_cell = y_origin + row * self.grid_size
                    x_max_cell = x_min_cell + self.grid_size
                    y_max_cell = y_min_cell + self.grid_size

                    inter_x = max(feat_x_min, x_min_cell)
                    inter_y = max(feat_y_min, y_min_cell)
                    inter_x2 = min(feat_x_max, x_max_cell)
                    inter_y2 = min(feat_y_max, y_max_cell)
                    inter_area = max(0.0, inter_x2 - inter_x) * max(0.0, inter_y2 - inter_y)
                    cell_area = self.grid_size * self.grid_size
                    cell_coverage = inter_area / cell_area if cell_area > 0 else 0.0

                    if cell_id not in cell_map:
                        cell_map[cell_id] = {
                            "cell_id": cell_id,
                            "x_min": x_min_cell,
                            "y_min": y_min_cell,
                            "sum_cov_weight": 0.0,
                            "sum_weight": 0.0,
                            "features": set(),
                        }

                    slot = cell_map[cell_id]
                    slot["sum_cov_weight"] += feat.coverage * cell_coverage
                    slot["sum_weight"] += cell_coverage
                    slot["features"].add(feat.type)

        cells = []
        for cell_data in cell_map.values():
            sum_weight = cell_data["sum_weight"]
            avg_coverage = (cell_data["sum_cov_weight"] / sum_weight) if sum_weight > 0 else 0.0
            cells.append(
                {
                    "cell_id": cell_data["cell_id"],
                    "x_min": round(cell_data["x_min"], 3),
                    "y_min": round(cell_data["y_min"], 3),
                    "x_max": round(cell_data["x_min"] + self.grid_size, 3),
                    "y_max": round(cell_data["y_min"] + self.grid_size, 3),
                    "coverage": round(avg_coverage, 4),
                    "design_coverage": round(avg_coverage, 4),
                    "features": list(cell_data["features"]),
                }
            )

        cells.sort(key=lambda c: c["cell_id"])

        out = {
            "source": source,
            "total_features": len(features),
            "grid_size": self.grid_size,
            "x_origin": round(x_origin, 3),
            "y_origin": round(y_origin, 3),
            "cell_count": len(cells),
            "cells": cells,
        }
        if mapping_stats is not None:
            out["mapping_stats"] = mapping_stats
        return out

    # -------------------------------------------------------------------------
    # DWG 模式（需要 LibreDWG）
    # -------------------------------------------------------------------------

    def extract_from_dwg(self, dwg_path: str) -> Dict[str, Any]:
        """
        从 DWG 文件提取设计端网格。

        优先使用 ogr2ogr (GDAL) 转换为 GeoJSON。
        若不可用，则自动回退到 LibreDWG 的 dwgread -O GeoJSON。

        Args:
            dwg_path: DWG 文件路径

        Returns:
            grid_design.json 兼容的字典
        """
        if not os.path.exists(dwg_path):
            raise FileNotFoundError(f"DWG 文件不存在: {dwg_path}")

        with tempfile.TemporaryDirectory(prefix="dwg_extract_") as tmp_dir:
            geojson_path = os.path.join(tmp_dir, "dwg_output.geojson")
            self._convert_dwg_to_geojson(dwg_path, geojson_path)

            if not os.path.exists(geojson_path):
                raise RuntimeError(f"DWG 转换后未生成输出文件: {geojson_path}")

            features, mapping_stats = self._parse_geojson(geojson_path)
            return self._build_design_grid_from_geojson(features, mapping_stats)

    def _convert_dwg_to_geojson(self, dwg_path: str, geojson_path: str) -> None:
        """将 DWG 转换为 GeoJSON（支持工具优先级配置）。"""
        if self.prefer_tool == "ogr2ogr":
            ogr_path = self._find_ogr2ogr()
            if ogr_path:
                self._convert_with_ogr2ogr(ogr_path, dwg_path, geojson_path)
                return
            dwgread_path = self._find_dwgread()
            if dwgread_path:
                self._convert_with_dwgread(dwgread_path, dwg_path, geojson_path)
                return
        elif self.prefer_tool == "dwgread":
            dwgread_path = self._find_dwgread()
            if dwgread_path:
                self._convert_with_dwgread(dwgread_path, dwg_path, geojson_path)
                return
            ogr_path = self._find_ogr2ogr()
            if ogr_path:
                self._convert_with_ogr2ogr(ogr_path, dwg_path, geojson_path)
                return
        else:
            ogr_path = self._find_ogr2ogr()
            if ogr_path:
                self._convert_with_ogr2ogr(ogr_path, dwg_path, geojson_path)
                return
            dwgread_path = self._find_dwgread()
            if dwgread_path:
                self._convert_with_dwgread(dwgread_path, dwg_path, geojson_path)
                return

        raise RuntimeError(
            "未找到可用 DWG 转换工具。\n"
            "可选方案：\n"
            "1) 安装 GDAL（提供 ogr2ogr）\n"
            "2) 使用仓库自带的 libredwg-0.13.4-win64.zip，并确保可调用 dwgread.exe"
        )

    def _convert_with_ogr2ogr(self, ogr_path: str, dwg_path: str, geojson_path: str) -> None:
        """使用 ogr2ogr 转换 DWG -> GeoJSON。"""
        try:
            result = subprocess.run(
                [ogr_path, "-f", "GeoJSON", geojson_path, dwg_path],
                capture_output=True,
                text=True,
                timeout=self.tool_timeout_sec,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ogr2ogr 转换失败: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("ogr2ogr 超时（>300秒），DWG 文件可能过大或格式有问题")

    def _convert_with_dwgread(self, dwgread_path: str, dwg_path: str, geojson_path: str) -> None:
        """使用 LibreDWG dwgread 转换 DWG -> GeoJSON。"""
        tool_dir = os.path.dirname(os.path.abspath(dwgread_path))
        env = os.environ.copy()
        env["PATH"] = f"{tool_dir}{os.pathsep}{env.get('PATH', '')}"

        try:
            with open(geojson_path, "w", encoding="utf-8") as out_f:
                result = subprocess.run(
                    [dwgread_path, "-O", "GeoJSON", dwg_path],
                    stdout=out_f,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self.tool_timeout_sec,
                    env=env,
                )
            if result.returncode != 0:
                raise RuntimeError(f"dwgread 转换失败: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("dwgread 超时（>300秒），DWG 文件可能过大或格式有问题")

    def _find_ogr2ogr(self) -> Optional[str]:
        """查找可用 ogr2ogr 路径（参数 > 环境变量 > 策略候选 > 默认候选 > PATH）。"""
        import shutil as sh

        env_path = os.getenv("OGR2OGR_PATH")
        env_libredwg_dir = os.getenv("LIBREDWG_WIN64_DIR")

        candidates: List[str] = []

        if self.ogr2ogr_path:
            candidates.append(self.ogr2ogr_path)
        if env_path:
            candidates.append(env_path)

        candidates.extend(self.ogr2ogr_candidates)

        if self.libredwg_dir:
            candidates.append(os.path.join(self.libredwg_dir, "ogr2ogr.exe"))
        if env_libredwg_dir:
            candidates.append(os.path.join(env_libredwg_dir, "ogr2ogr.exe"))

        candidates.extend([
            os.path.join("E:/working2026/CADCompareWith3D/dwg", "mingw64", "bin", "ogr2ogr.exe"),
            "ogr2ogr",
            "ogr2ogr.exe",
            "ogr2ogr.bat",
        ])

        for name in candidates:
            path = self._resolve_candidate_path(name, sh)
            if path and self._is_command_usable(path, ["--version"]):
                return path
        return None

    def _find_dwgread(self) -> Optional[str]:
        """查找可用 dwgread 路径（参数 > 环境变量 > 策略候选 > 默认候选 > PATH）。"""
        import shutil as sh

        env_path = os.getenv("DWGREAD_PATH")
        env_libredwg_dir = os.getenv("LIBREDWG_WIN64_DIR")

        candidates: List[str] = []

        if self.dwgread_path:
            candidates.append(self.dwgread_path)
        if env_path:
            candidates.append(env_path)

        candidates.extend(self.dwgread_candidates)

        if self.libredwg_dir:
            candidates.append(os.path.join(self.libredwg_dir, "dwgread.exe"))
        if env_libredwg_dir:
            candidates.append(os.path.join(env_libredwg_dir, "dwgread.exe"))

        candidates.extend([
            os.path.join("E:/working2026/CADCompareWith3D/dwg_reader_c", "libredwg_win64", "dwgread.exe"),
            os.path.join("E:/working2026/CADCompareWith3D/dwg_reader_c", "dwgread.exe"),
            "dwgread",
            "dwgread.exe",
        ])

        for name in candidates:
            path = self._resolve_candidate_path(name, sh)
            if path and self._is_command_usable(path, ["--version"]):
                return path
        return None

    def _resolve_candidate_path(self, name: str, sh) -> Optional[str]:
        """解析候选命令路径。"""
        if not name:
            return None
        if os.path.isabs(name) or os.path.sep in name or "/" in name:
            return name if os.path.exists(name) else None
        return sh.which(name)

    def _is_command_usable(self, cmd_path: str, args: List[str]) -> bool:
        """探测命令是否可执行（含依赖库可加载）。"""
        env = os.environ.copy()
        cmd_dir = os.path.dirname(os.path.abspath(cmd_path))
        env["PATH"] = f"{cmd_dir}{os.pathsep}{env.get('PATH', '')}"

        try:
            result = subprocess.run(
                [cmd_path, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                env=env,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_geojson(self, geojson_path: str) -> tuple[List[DesignFeature], Dict[str, Any]]:
        """从 GeoJSON 文件提取 DesignFeature 列表和图层映射统计。"""
        with open(geojson_path, "r", encoding="utf-8") as f:
            geojson = json.load(f)

        mapping_stats: Dict[str, Any] = {
            "enabled": self.layer_mapping_enabled,
            "total_features": 0,
            "mapped_features": 0,
            "unknown_features": 0,
            "ignored_features": 0,
            "rules_hit": {},
        }

        features: List[DesignFeature] = []
        for feat in geojson.get("features", []):
            mapping_stats["total_features"] += 1

            geom = feat.get("geometry")
            if not geom:
                continue

            props = feat.get("properties", {})
            raw_layer = props.get("Layer", props.get("layer", props.get("type", "unknown")))
            layer_name = str(raw_layer) if raw_layer is not None else "unknown"

            mapped_type, map_meta = self._map_layer_to_component(layer_name)
            if map_meta.get("ignored"):
                mapping_stats["ignored_features"] += 1
                continue
            if map_meta.get("mapped"):
                mapping_stats["mapped_features"] += 1
                rule_name = map_meta.get("rule", "")
                if rule_name:
                    mapping_stats["rules_hit"][rule_name] = mapping_stats["rules_hit"].get(rule_name, 0) + 1
            if map_meta.get("unknown"):
                mapping_stats["unknown_features"] += 1

            geom_type = (geom.get("type") or "").lower()
            coords = geom.get("coordinates", [])
            bbox = self._extract_bbox(geom_type, coords)
            if bbox is None:
                continue

            x_min, y_min, x_max, y_max = bbox
            features.append(
                DesignFeature(
                    type=mapped_type,
                    x_min=x_min,
                    y_min=y_min,
                    x_max=x_max,
                    y_max=y_max,
                    coverage=self.default_coverage,
                    elevation=0.0,
                )
            )

        mapping_stats["rules_hit"] = dict(sorted(mapping_stats["rules_hit"].items(), key=lambda kv: kv[0]))
        return features, mapping_stats

    def _extract_bbox(self, geom_type: str, coords: Any) -> Optional[Tuple[float, float, float, float]]:
        """从几何坐标中提取外包矩形。"""
        x_min = float("inf")
        y_min = float("inf")
        x_max = float("-inf")
        y_max = float("-inf")

        def update(pt: Any) -> None:
            nonlocal x_min, y_min, x_max, y_max
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                return
            x = pt[0]
            y = pt[1]
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return
            x = float(x)
            y = float(y)
            x_min = min(x_min, x)
            y_min = min(y_min, y)
            x_max = max(x_max, x)
            y_max = max(y_max, y)

        if geom_type == "polygon":
            for ring in coords or []:
                for pt in ring or []:
                    update(pt)
        elif geom_type == "multipolygon":
            for poly in coords or []:
                for ring in poly or []:
                    for pt in ring or []:
                        update(pt)
        elif geom_type == "linestring":
            for pt in coords or []:
                update(pt)
        elif geom_type == "multilinestring":
            for line in coords or []:
                for pt in line or []:
                    update(pt)
        elif geom_type == "point":
            update(coords)
        else:
            return None

        if x_min == float("inf"):
            return None
        return x_min, y_min, x_max, y_max

    def _normalize_layer_name(self, name: Any) -> str:
        if name is None:
            return ""
        return str(name).strip().lower()

    def _compile_layer_mapping_rules(self, rules: Any) -> List[Dict[str, Any]]:
        compiled: List[Dict[str, Any]] = []
        if not isinstance(rules, list):
            return compiled

        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue

            match_mode = str(rule.get("match", "")).strip().lower()
            target_type = str(rule.get("type", "")).strip()
            if match_mode not in {"exact", "contains", "regex"} or not target_type:
                continue

            if match_mode == "regex":
                pattern = rule.get("pattern")
                if not isinstance(pattern, str) or not pattern:
                    continue
                try:
                    regex = re.compile(pattern)
                except re.error:
                    continue
                rule_name = f"regex:{pattern}"
                compiled.append(
                    {
                        "match": "regex",
                        "type": target_type,
                        "regex": regex,
                        "name": rule_name,
                        "index": idx,
                    }
                )
            else:
                value = rule.get("value")
                if not isinstance(value, str) or not value:
                    pattern = rule.get("pattern")
                    if isinstance(pattern, str) and pattern:
                        value = pattern
                    else:
                        continue
                normalized_value = self._normalize_layer_name(value)
                rule_name = f"{match_mode}:{normalized_value}"
                compiled.append(
                    {
                        "match": match_mode,
                        "type": target_type,
                        "value": normalized_value,
                        "name": rule_name,
                        "index": idx,
                    }
                )

        compiled.sort(key=lambda x: x["index"])
        return compiled

    def _map_layer_to_component(self, layer_name: Any) -> Tuple[str, Dict[str, Any]]:
        normalized = self._normalize_layer_name(layer_name)

        if self.layer_mapping_enabled and normalized in self.layer_mapping_ignore_layers:
            return self.layer_mapping_unknown_type, {
                "ignored": True,
                "mapped": False,
                "unknown": False,
                "rule": "",
            }

        if self.layer_mapping_enabled:
            for rule in self.layer_mapping_rules:
                if rule["match"] == "exact":
                    if normalized == rule["value"]:
                        return rule["type"], {
                            "ignored": False,
                            "mapped": True,
                            "unknown": False,
                            "rule": rule["name"],
                        }
                elif rule["match"] == "contains":
                    if rule["value"] and rule["value"] in normalized:
                        return rule["type"], {
                            "ignored": False,
                            "mapped": True,
                            "unknown": False,
                            "rule": rule["name"],
                        }
                elif rule["match"] == "regex":
                    if rule["regex"].search(str(layer_name or "")):
                        return rule["type"], {
                            "ignored": False,
                            "mapped": True,
                            "unknown": False,
                            "rule": rule["name"],
                        }

        if self.layer_mapping_enabled:
            return self.layer_mapping_unknown_type, {
                "ignored": False,
                "mapped": False,
                "unknown": True,
                "rule": "",
            }

        fallback = str(layer_name) if layer_name is not None else "unknown"
        return fallback, {
            "ignored": False,
            "mapped": False,
            "unknown": False,
            "rule": "",
        }

    def _build_design_grid_from_geojson(
        self,
        features: List[DesignFeature],
        mapping_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从 GeoJSON 解析结果构建网格（与 CSV 模式相同逻辑）。"""
        return self._build_design_grid(features, source="dwg", mapping_stats=mapping_stats)

    # -------------------------------------------------------------------------
    # 工具方法
    # -------------------------------------------------------------------------

    def save_json(self, grid_data: Dict[str, Any], path: str) -> None:
        """保存设计端网格为 JSON 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(grid_data, f, ensure_ascii=False, indent=2)


# -------------------------------------------------------------------------
# CLI 入口
# -------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="设计端 CAD 几何提取工具")
    parser.add_argument("input", help="DWG 文件路径 或 CSV 标注文件路径")
    parser.add_argument("--grid-size", type=float, default=10.0, help="网格边长（米）")
    parser.add_argument("--output", default="grid_design.json", help="输出 JSON 路径")
    parser.add_argument("--libredwg-dir", default=None, help="libredwg_win64 目录路径")
    parser.add_argument("--dwgread-path", default=None, help="dwgread 可执行文件路径")
    parser.add_argument("--ogr2ogr-path", default=None, help="ogr2ogr 可执行文件路径")
    parser.add_argument("--tool-timeout-sec", type=int, default=None, help="DWG 工具转换超时（秒）")
    parser.add_argument("--prefer-tool", choices=["auto", "ogr2ogr", "dwgread"], default="auto", help="DWG 转换工具优先级")
    parser.add_argument("--default-coverage", type=float, default=0.8, help="DWG 特征默认覆盖得分（0~1）")
    args = parser.parse_args()

    timeout_from_env = os.getenv("DWG_TOOL_TIMEOUT_SEC")
    effective_timeout = args.tool_timeout_sec
    if effective_timeout is None and timeout_from_env:
        try:
            effective_timeout = int(timeout_from_env)
        except ValueError:
            effective_timeout = None

    extractor = DWGGeometryExtractor(
        grid_size=args.grid_size,
        libredwg_dir=args.libredwg_dir,
        dwgread_path=args.dwgread_path,
        ogr2ogr_path=args.ogr2ogr_path,
        tool_timeout_sec=effective_timeout or 300,
        prefer_tool=args.prefer_tool,
        default_coverage=args.default_coverage,
    )

    # 自动判断输入类型
    if args.input.lower().endswith(".csv"):
        print(f"使用 CSV 模式: {args.input}")
        grid = extractor.extract_from_csv(args.input)
    else:
        print(f"尝试 DWG 模式: {args.input}")
        try:
            grid = extractor.extract_from_dwg(args.input)
        except RuntimeError as e:
            print(f"警告: {e}")
            print("\n提示: 将 DWG 文件转换为 CSV 标注格式，格式如下：")
            print("type,x_min,y_min,x_max,y_max,coverage,elevation")
            print("flood_wall,500000,3500010,500020,3500030,0.85,1852.5")
            print("spillway,500030,3500040,500080,3500050,0.6,1854.0")
            print("\n然后使用 CSV 模式重新运行：")
            print(f"  python dwg_geometry_extractor.py design_annotations.csv --output grid_design.json")
            return

    print(f"提取完成: {grid.get('total_features', grid.get('cell_count', 0))} 个构件，"
          f"{len(grid.get('cells', []))} 个网格格子")
    extractor.save_json(grid, args.output)
    print(f"已保存 -> {args.output}")


if __name__ == "__main__":
    main()