# 水利工程 CAD vs 实景 对比引擎

## 项目概述

对比引擎将**设计端 CAD 数据**（DWG/PDF）与**实景端 3D 测量数据**进行匹配与偏差分析，输出风险事件报告，支持可选的 LLM 大模型复核。

适用场景：水利工程施工质量巡查、竣工验收比对。

---

## 目录结构

```
/
├── compare_engine.py        # 核心比对引擎：对象匹配 + 风险规则评估
├── input_normalizer.py      # 输入数据归一化（DWG/PDF 来源选择、字段标准化）
├── policy_loader.py        # 策略配置加载与合并（默认策略 + 用户策略）
├── llm_reviewer.py         # LLM 复核器（OpenAI 兼容 /chat/completions 接口）
├── run_compare.py          # CLI 入口，组装流程并输出摘要
│
├── risk_thresholds_v0.json # 风险规则集（阈值 + 处置动作）
├── risk_thresholds_v0.yaml # 同上 YAML 版本
│
├── policy_v1.json          # 策略配置示例（LLM 关闭）
├── policy_qwen_always.json # 策略配置示例（LLM always 模式）
├── policy_qianwen_3_5_plus.json
├── policy_v1_llm_always.json
│
├── sample_design.json      # 设计端输入示例
├── sample_design_mixed.json # 设计端输入示例（含 DWG + PDF 混合）
├── sample_design_single.json
├── sample_reality.json    # 实景端输入示例
│
├── result_*.json          # 运行结果示例（调试用）
│
├── 数据库表结构_MySQL.sql  # MySQL 版表结构
├── 数据库表结构_PostGIS.sql # PostGIS 空间数据库版表结构
├── migration_up.sql       # 迁移脚本（增量表）
├── migration_down.sql
├── migration_alter_v2_up_mysql.sql
├── migration_alter_v2_down_mysql.sql
│
├── openapi.yaml           # API 接口规范
├── API清单草案_水利工程.md
└── poc_test_page.html/js/css # 轻量 Web 可视化 POC（非生产级）
```

---

## 数据模型

### 设计端对象（design）

```json
{
  "id": "sluice_axis_01",
  "type": "sluice_axis",
  "location": "闸室1段",
  "x": 1000.0, "y": 2000.0, "z": 35.00,
  "length_m": 48.0, "width_m": 12.0, "height_m": 18.0,
  "source_type": "dwg",
  "confidence": 1.0,
  "trace": {}
}
```

**关键字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识，用于 ID 精确匹配 |
| `type` | string | 对象类型，用于类型过滤 |
| `x`, `y`, `z` | float | 坐标（单位：米） |
| `length_m`, `width_m`, `height_m` | float | 几何尺寸（单位：米） |
| `source_type` | string | 数据来源：`dwg`（默认）或 `pdf` |
| `confidence` | float | 数据置信度（0~1） |
| `trace` | object | 元数据，可含 `image_urls`、`merged_from` 等 |

### 实景端对象（reality）

字段结构同设计端，`source_type` 固定为 `reality`。

### 风险事件（event）

```json
{
  "rule_id": "GEO_PLANAR_OFFSET",
  "risk_type": "关键构件平面偏移",
  "level": "high",
  "location": "闸室1段",
  "suggestion": "现场复核测量",
  "evidence": {
    "design_id": "sluice_axis_01",
    "reality_id": "sluice_axis_01_r",
    "metric": "planar_offset_m",
    "metric_value": 0.22,
    "thresholds": { "low": 0.05, "medium": 0.10, "high": 0.20, "critical": 0.30 }
  },
  "match_confidence": 0.82,
  "source_type": "dwg",
  "source_confidence": 1.0,
  "manual_review_required": true,
  "llm_reviewed": false,
  "review_source": "rule_engine"
}
```

---

## 核心流程

```
design.json + reality.json + rules.json + policy.json
       │
       ▼
┌─────────────────────┐
│  input_normalizer   │  归一化字段、过滤禁用来源（DWG/PDF 选择）、
│                     │  同 ID 多来源时按 prefer_source 合并
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   match_objects     │  贪心匹配：ID → type → 距离 → 尺寸
│                     │  输出 matched / missing_design / unplanned_reality
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  run_diff_and_risk   │  对每对 matched 对象：
│                     │  - GEO_PLANAR_OFFSET（平面偏移）
│                     │  - GEO_ELEVATION_DEVIATION（高程偏差）
│                     │  - DIMENSION_DEVIATION_RATE（尺寸偏差率）
│                     │  - STR_LOCAL_DEFORMATION（局部变形）
│                     │  对 missing/unplanned 计数触发：
│                     │  - SEM_MISSING_CONSTRUCTION（漏建）
│                     │  - SEM_UNPLANNED_CONSTRUCTION（疑似违建）
│                     │  应用 risk_gate（强制人工复核条件）
│                     │  触发 LLM 复核（条件性 / 全量）
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  输出 JSON 报告      │  summary + events + llm_reviews
└─────────────────────┘
```

---

## 匹配算法（`compare_engine.match_objects`）

| 维度 | 权重 | 说明 |
|------|------|------|
| `S_id` | 0.35 | ID 完全一致 = 1.0，否则 0.0 |
| `S_type` | 0.20 | type 完全一致 = 1.0，否则 0.0 |
| `S_dist` | 0.20 | 距离得分：`1 - dist / max_match_distance_m` |
| `S_dim` | 0.15 | 尺寸平均得分：`1 - |d - r| / d` |
| `S_src` | 0.10 | 来源权重：DWG=1.0，PDF=0.7 |

**匹配条件**：同 type 或同 ID，且 `distance_m ≤ max_match_distance_m` 或 `S_id=1.0`。

---

## 风险等级与阈值（`risk_thresholds_v0.json`）

| 规则 ID | 指标 | low | medium | high | critical | 比较 |
|---------|------|-----|--------|------|----------|------|
| `GEO_PLANAR_OFFSET` | 平面偏移 m | 0.05 | 0.10 | 0.20 | 0.30 | `>` |
| `GEO_ELEVATION_DEVIATION` | 高程偏差 m | 0.03 | 0.08 | 0.15 | 0.25 | `>` |
| `DIMENSION_DEVIATION_RATE` | 尺寸偏差率 | 0.5% | 1.0% | 2.0% | 3.0% | `>` |
| `STR_LOCAL_DEFORMATION` | 变形量 m | — | — | 0.10 | 0.20 | `>`（需同时满足面积≥2m²） |
| `SEM_MISSING_CONSTRUCTION` | 漏建数量 | — | ≥1 | ≥3 | ≥5 | `>=` |
| `SEM_UNPLANNED_CONSTRUCTION` | 疑似违建数量 | — | — | ≥1 | ≥3 | `>=` |

---

## 策略配置（policy）

### input_policy

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `dwg_enabled` | true | 是否启用 DWG 来源 |
| `pdf_enabled` | true | 是否启用 PDF 来源 |
| `pdf_mode` | optional | `optional` / `required` / `disabled` |
| `prefer_source` | dwg | 同 ID 多来源时优先选哪个：`dwg` / `pdf` / `merge` |
| `max_match_distance_m` | 10.0 | 最大匹配距离（米） |
| `min_match_score` | 0.45 | 最小匹配得分 |
| `source_weights` | {dwg:1.0, pdf:0.7} | 来源权重系数 |
| `dwg.registration.mode` | auto | 坐标配准模式：`off` / `auto` / `manual` |
| `dwg.layer_mapping.enabled` | true | 图层语义映射开关，支持忽略层与规则映射 |

### llm_policy

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | false | 是否启用 LLM 复核 |
| `review_mode` | off | `off` / `selective` / `always` |
| `low_match_threshold` | 0.65 | 触发复核的低匹配阈值 |
| `near_threshold_margin` | 0.02 | 临界值附近触发容差 |
| `triggers.low_match_confidence` | true | 低匹配置信度触发 |
| `triggers.pdf_source_near_threshold` | true | PDF 来源临界触发 |
| `triggers.rule_conflict` | true | 规则冲突触发 |
| `provider.base_url` | "" | LLM 服务地址 |
| `provider.api_key_env` | LLM_API_KEY | API Key 环境变量名 |
| `provider.model` | qianwen-3.5-plus | 模型名称 |
| `provider.timeout_seconds` | 30 | 请求超时秒数 |

### risk_gate

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `min_confidence_for_auto_close` | 0.75 | 自动关闭最小置信度 |
| `force_manual_review_levels` | [high, critical] | 强制人工复核等级 |
| `force_manual_review_when_pdf_low_conf` | true | PDF 低置信度强制复核 |

---

## CLI 使用

```bash
# 纯规则引擎对比
python run_compare.py \
  --design sample_design.json \
  --reality sample_reality.json \
  --rules risk_thresholds_v0.json \
  --out result.json

# 启用 LLM 复核（使用策略文件中的配置）
python run_compare.py \
  --design sample_design.json \
  --reality sample_reality.json \
  --rules risk_thresholds_v0.json \
  --policy policy_qwen_always.json \
  --out result_llm.json

# 命令行强制开启 LLM（覆盖策略文件）
python run_compare.py \
  --design sample_design.json \
  --reality sample_reality.json \
  --rules risk_thresholds_v0.json \
  --policy policy_v1.json \
  --llm \
  --out result_llm.json
```

---

## LLM 复核接口要求

`llm_reviewer.py` 发送 OpenAI 兼容 `/chat/completions` 请求，期望返回 JSON：

```json
{
  "risk_interpretation": "该偏差位于高风险区域...",
  "evidence_refs": ["《GB 50201》相关条文"],
  "confidence_delta": 0.05,
  "recommended_action": "现场复核测量"
}
```

若返回格式不合法或网络超时，自动回退到规则引擎结论（不阻断流程）。

---

## 关键行为说明

1. **同 ID 多来源合并**：若 `prefer_source=dwg`，同 ID 的 PDF 对象被合并丢弃，合并来源记录在 `trace.merged_from`。
2. **LLM 触发条件**：`review_mode=selective` 时仅对低置信度 / PDF 临界 / 规则冲突事件触发复核；`always` 时全量复核。
3. **PDF 强制复核**：`force_pdf_low_conf=true` 时，来源为 PDF 且置信度低于阈值的事件强制人工复核，不经过 LLM。
4. **变形检测**：`STR_LOCAL_DEFORMATION` 为复合条件，需同时满足变形量 ≥ 阈值 **且** 面积 ≥ `min_area_m2`。
5. **语义事件聚合**：`SEM_MISSING_CONSTRUCTION` 和 `SEM_UNPLANNED_CONSTRUCTION` 以项目维度聚合，输出缺失/违建对象 ID 列表，不逐对象拆解。

---

## 数据库表结构

- **MySQL 版**：`数据库表结构_MySQL.sql`
- **PostGIS 版**：`数据库表结构_PostGIS.sql`（含空间索引）
- 增量迁移：`migration_*.sql`

---

## 已知限制

- 当前为单地域坐标匹配，未处理投影坐标系转换（如 CGCS2000 / WGS84 混用）
- `match_objects` 为贪心算法，不保证全局最优匹配
- PDF 来源仅支持字段映射，未实现 PDF 文本解析
- LLM API Key 默认从环境变量读取，硬编码 key 建议使用 `api_key_env`
- POC Web 页面（`poc_test_page.*`）为演示用，非生产级前端

---

## 扩展指引

### 添加新的风险规则

在 `risk_thresholds_v0.json` 的 `rules` 数组中添加条目：

```json
{
  "id": "CUSTOM_RULE_01",
  "name": "自定义规则",
  "category": "geometry",
  "enabled": true,
  "metric": "metric_key",
  "thresholds": { "high": 1.0, "critical": 2.0 },
  "comparison": ">",
  "action": {
    "high": "现场复核",
    "critical": "暂停施工"
  }
}
```

并在 `compare_engine.py` 的 `run_diff_and_risk` 函数的规则遍历循环中添加对应的 `metric_name` 映射。

### 切换 LLM 提供商

修改 `policy_*.json` 中的 `llm_policy.provider`：

- `base_url`：服务商 base URL（末尾 `/v1` 等不含路径）
- `api_key_env`：环境变量名（优先）或 `api_key` 直接填入
- `model`：模型名称（需与提供商兼容）
- `endpoint_path`：默认 `/chat/completions`（OpenAI 兼容）

---

## 3D Tiles 实景对比（扩展模块）

本引擎支持将 OSGBLab 生成的 3D Tiles（B3DM + Draco 压缩）与 CAD 施工图进行对比，适用于水利工程竣工验收场景。

### 环境要求

- **Python 3.8+**（方案 A 网格对比）
- **Node.js 16+** + **draco3d npm 包**（方案 B 点云解码）

```bash
# Node.js 依赖
npm install draco3d
```

### 方案 A：网格对比（推荐）

将设计和实景都投影到规则网格（默认 10m×10m），按格子对比覆盖得分。

```bash
python run_grid_compare.py \
    --tileset https://example.com/tileset.json \
    --design design_annotations.csv \
    --grid-size 10.0 \
    --threshold 0.3 \
    --output ./results/
```

如需控制 DWG 工具链（跨机器环境固化），可增加参数：

```bash
python run_grid_compare.py \
    --tileset ./test_file/test.json \
    --design "./test_file/14、施工图设计文件/3.2  溢洪道交通桥图纸04.dwg" \
    --policy ./policy_v1.json \
    --libredwg-dir "./dwg_reader_c/libredwg_win64" \
    --prefer-tool dwgread \
    --tool-timeout-sec 300 \
    --baseline-report ./checkpoints/dwg_validate/e2e_full_baseline_report.json \
    --output ./checkpoints/dwg_validate/e2e_full_latest
```

**设计端标注 CSV 格式：**

```csv
type,x_min,y_min,x_max,y_max,coverage,elevation
flood_wall,500000,3500010,500020,3500030,0.85,1852.5
spillway,500030,3500040,500080,3500050,0.6,1854.0
gate_chamber,500100,3500055,500150,3500070,0.9,1853.2
```

**输出：**
- `grid_reality.json` — 实景端网格
- `grid_design.json` — 设计端网格
- `grid_compare_result.json` — 对比结果（含 missing_reality / missing_design / deviation 事件）

### 方案 B：点云采样对比

从 B3DM 解码点云，与设计线框求交检测偏差。

```bash
# 1. 批量采样点云
python pointcloud_sampler.py https://example.com/tileset.json \
    --output pointcloud.json --max-tiles 20 --max-points 500

# 2. 执行点云对比
python pointcloud_compare.py \
    --pointcloud pointcloud.json \
    --design design_geometry.json \
    --output pointcloud_result.json
```

### 坐标配准机制（DWG 网格）

`run_grid_compare.py` 已内置设计网格到实景网格的配准流程，配置位于 `policy.input_policy.dwg.registration`。

- `mode=off`：关闭配准，直接对比
- `mode=auto`：自动尝试 scale+offset 候选，按重叠提升选择最优方案
- `mode=manual`：使用 `manual_transform` 指定 `scale_x/scale_y/dx/dy`

配准结果写入 `baseline_report.registration`，包括：
- `applied` / `reason`
- `transform`
- `before_overlap_ratio` / `after_overlap_ratio`
- `before_bbox_overlap_ratio` / `after_bbox_overlap_ratio`
- `candidate_count`

### 图层语义映射配置示例

`dwg_geometry_extractor.py` 支持按图层名映射到标准构件语义，并输出 `mapping_stats` 用于回归观察。

```json
{
  "input_policy": {
    "dwg": {
      "layer_mapping": {
        "enabled": true,
        "unknown_type": "unknown",
        "ignore_layers": ["0", "DEFPOINTS", "标注"],
        "rules": [
          {"match": "regex", "pattern": "(?i)spillway|溢洪道", "type": "spillway"},
          {"match": "contains", "value": "闸室", "type": "gate_chamber"},
          {"match": "exact", "value": "导流洞", "type": "tunnel"}
        ]
      }
    }
  }
}
```

### 性能优化与回归门禁（更新）

已完成两项性能优化：
- `_parse_geojson` 使用单次遍历计算 bbox，减少中间列表
- `_build_design_grid` 使用 `sum_cov_weight/sum_weight` 聚合，减少列表累积

回归脚本新增耗时统计与门禁参数：
- `elapsed_stats_sec.min/max/mean/median/p50/p95`
- `performance_gate.max_elapsed_p50_sec`
- `performance_gate.max_elapsed_p95_sec`

示例：

```bash
python checkpoints/dwg_validate/run_dwg_regression.py \
    --baseline-json checkpoints/dwg_validate/real_dwg_read_verify_summary.json \
    --output-json checkpoints/dwg_validate/regression_latest.json \
    --max-feature-drift-ratio 0.02 \
    --max-non-null-drift-ratio 0.02 \
    --min-pass-rate 1.0 \
    --max-elapsed-p50-sec 60 \
    --max-elapsed-p95-sec 300
```


#### 1) 12 个 DWG 一键回归

```bash
python checkpoints/dwg_validate/run_dwg_regression.py \
    --baseline-json checkpoints/dwg_validate/real_dwg_read_verify_summary.json \
    --output-json checkpoints/dwg_validate/regression_latest.json \
    --max-feature-drift-ratio 0.02 \
    --max-non-null-drift-ratio 0.02 \
    --min-pass-rate 1.0
```

输出文件：`checkpoints/dwg_validate/regression_latest.json`

核心判定字段：
- `overall_pass`：整体是否通过
- `failed_files`：失败文件数
- `drift_summary.feature_drift_exceeded`：特征数漂移超阈值数
- `drift_summary.non_null_drift_exceeded`：非空几何数漂移超阈值数
- `elapsed_stats_sec.p50 / p95`：回归耗时分位统计
- `performance_gate.p50_pass / p95_pass`：性能门禁是否通过

漂移规则：
- `feature_drift_ratio = abs(cur_features - baseline_features) / max(1, baseline_features)`
- `non_null_drift_ratio = abs(cur_non_null - baseline_non_null) / max(1, baseline_non_null)`

#### 2) 完整链路基线报告（tileset + dwg）

```bash
python checkpoints/dwg_validate/run_full_chain_baseline.py \
    --tileset ./test_file/test.json \
    --design "./test_file/14、施工图设计文件/3.2  溢洪道交通桥图纸04.dwg" \
    --output checkpoints/dwg_validate/e2e_full_latest \
    --baseline-report checkpoints/dwg_validate/e2e_full_baseline_report.json
```

输出文件：
- `checkpoints/dwg_validate/e2e_full_latest/grid_reality.json`
- `checkpoints/dwg_validate/e2e_full_latest/grid_design.json`
- `checkpoints/dwg_validate/e2e_full_latest/grid_compare_result.json`
- `checkpoints/dwg_validate/e2e_full_baseline_report.json`

基线报告关键字段：
- `elapsed_sec`、`steps_elapsed_sec`（阶段耗时）
- `grid_design.total_features / cell_count`
- `grid_reality.total_tiles / cell_count`
- `compare_summary`、`events_count`
- `registration.applied / reason / transform`
- `registration.before_overlap_ratio / after_overlap_ratio`

### 文件说明

| 文件 | 用途 |
|------|------|
| `tileset_parser.py` | 解析 tileset.json，提取 Tile 元数据 |
| `b3dm_reader.py` | 读取 B3DM header（提取 RTC 中心） |
| `draco_decoder.js` | Node.js Draco 压缩解码（draco3d npm） |
| `grid_builder.py` | 将 Tile 列表网格化（方案 A） |
| `grid_compare.py` | 网格对比引擎 |
| `dwg_geometry_extractor.py` | 设计端 CAD 几何提取（CSV/DWG） |
| `run_grid_compare.py` | 方案 A 完整流程 CLI 入口 |
| `pointcloud_sampler.py` | 批量采样点云（方案 B） |
| `pointcloud_compare.py` | 点云 vs 设计线框求交检测（方案 B） |

### 已知限制

1. **DWG 解析**：需系统安装 GDAL/ogr2ogr，否则自动降级到 CSV 模式
2. **R-tree**：`pointcloud_compare.py` 依赖 `rtree` 库（无则暴力遍历）
3. **坐标系统**：所有数据必须使用同一坐标系（CGCS2000 投影，米）
