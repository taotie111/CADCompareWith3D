# 实施方案 + 实施清单（DWG主通道 + PDF可选 + LLM复核）

## 1. 建设目标（本次改造）

在现有“CAD对比实景”MVP基础上，完成以下升级：

1. 支持双输入：DWG（主）+ CAD PDF（可选/可跳过）
2. 建立统一对象中间模型，保证算法层只消费一种结构
3. 引入LLM“选择性复核”以提升语义准确率
4. 保持几何主算法（偏移/高程/尺寸）的确定性与可解释性
5. 增加风险门控：低置信样本与高风险事件强制人工复核

---

## 2. 总体架构（改造后）

### 2.1 分层

- **输入层**：DWG Parser、PDF Parser（矢量优先，OCR兜底）
- **标准化层**：Canonical Object Builder（统一字段）
- **核心算法层**：match + diff + risk（确定性）
- **复核增益层**：LLM Gateway（仅处理低置信/冲突/临界）
- **门控层**：Risk Gate（自动闭环条件、人工复核条件）
- **应用层**：预警中心、工单闭环、审计追溯

### 2.2 决策原则

1. **DWG优先**：同对象冲突，DWG字段覆盖PDF字段
2. **PDF可跳过**：通过配置开关控制
3. **LLM不做最终裁决**：只做语义复核和建议生成
4. **高风险必复核**：high/critical默认人工确认

---

## 3. 配置设计

```yaml
input_policy:
  dwg_enabled: true
  pdf_enabled: true
  pdf_mode: optional   # optional | required | disabled
  pdf_skip_allowed: true
  prefer_source: dwg   # dwg | pdf | merge

llm_policy:
  enabled: true
  review_mode: selective  # selective | always | off
  triggers:
    low_match_confidence: true
    rule_conflict: true
    pdf_source_near_threshold: true
  output_schema_required: true
  evidence_reference_required: true

risk_gate:
  min_confidence_for_auto_close: 0.75
  force_manual_review_levels: [high, critical]
  force_manual_review_when_pdf_low_conf: true
```

---

## 4. 数据标准（统一中间模型）

```json
{
  "id": "sluice_axis_01",
  "type": "sluice_axis",
  "location": "闸室1段",
  "x": 1000.0,
  "y": 2000.0,
  "z": 35.0,
  "length_m": 48.0,
  "width_m": 12.0,
  "height_m": 18.0,
  "source_type": "dwg",
  "confidence": 0.95,
  "trace": {
    "file": "design_v1.dwg",
    "layer": "SLUICE_AXIS",
    "page": null,
    "ocr_tokens": []
  }
}
```

字段约束：
- `source_type`: `dwg|pdf`
- `confidence`: `[0,1]`
- `trace`: 必填，便于审计与回溯

---

## 5. 核心算法改造方案

## 5.1 对象匹配改造（match_objects）

当前：ID优先 + 同类型最近邻。  
改造：引入多因素匹配分值并输出匹配置信度。

匹配分值建议：

- `S_id`：ID是否一致（是=1，否=0）
- `S_type`：类型一致性（是=1，否=0）
- `S_dist`：距离得分 `max(0, 1 - d / d_max)`
- `S_dim`：尺寸相似度（1-平均相对误差）
- `S_src`：来源权重（DWG=1，PDF=0.7）

`S_total = 0.35*S_id + 0.20*S_type + 0.20*S_dist + 0.15*S_dim + 0.10*S_src`

输出新增：
- `match_confidence`
- `match_reason`

## 5.2 差异计算保持确定性

保留并增强当前指标：
- `planar_offset_m`
- `elevation_deviation_m`
- `dimension_deviation_rate`
- `deformation_m` + `deformation_area_m2`

## 5.3 规则引擎改造

规则命中后新增门控：
1. 若 `source_type=pdf` 且 `confidence < gate`，事件标记 `manual_review_required=true`
2. 若 `match_confidence < gate`，进入 LLM 复核队列
3. high/critical 一律人工复核

---

## 6. LLM接入方案（准确率增益）

## 6.1 触发条件

仅在以下场景调用LLM：
- 匹配低置信
- 规则冲突（同对象多规则矛盾）
- 临界阈值样本（如0.19m~0.22m）
- PDF提取对象语义不完整

## 6.2 输入最小化

出域字段：
- 对象类型、指标值、阈值、匿名位置描述、证据片段

不出域：
- 原始图纸文件、完整影像、精确坐标、涉密项目标识

## 6.3 输出要求（强Schema）

```json
{
  "normalized_object": {...},
  "risk_interpretation": "...",
  "evidence_refs": ["pdf#p12#line34", "obj:sluice_axis_01"],
  "confidence_delta": 0.08,
  "recommended_action": "..."
}
```

校验失败策略：自动重试1次；仍失败则回退本地模板说明。

---

## 7. API改造清单

## 7.1 新增/调整接口

1. `POST /ingest/design`  
   - 入参新增：`source_type=dwg|pdf`, `policy_id`
2. `POST /ingest/pdf/parse-jobs`  
   - 支持矢量/扫描识别策略
3. `POST /compare/jobs`  
   - 入参新增：`input_policy`, `llm_policy`
4. `GET /compare/results/{job_id}`  
   - 返回 `source_type`, `source_confidence`, `match_confidence`, `manual_review_required`
5. `POST /llm/review/jobs`  
   - 仅用于可疑样本复核

## 7.2 OpenAPI更新项

- 统一对象Schema新增字段：`source_type/confidence/trace`
- 风险事件Schema新增字段：`llm_reviewed/manual_review_required/evidence_refs`

---

## 8. MySQL表结构改造清单

在现有MySQL表基础上新增字段：

1. `design_objects`
- `source_type VARCHAR(16) NOT NULL DEFAULT 'dwg'`
- `source_confidence DECIMAL(5,4) NULL`
- `trace_json JSON NULL`

2. `diff_results`
- `match_confidence DECIMAL(5,4) NULL`
- `manual_review_required TINYINT(1) NOT NULL DEFAULT 0`
- `source_type VARCHAR(16) NULL`

3. `risk_events`
- `llm_reviewed TINYINT(1) NOT NULL DEFAULT 0`
- `review_source VARCHAR(32) NULL`  -- rule_engine | llm
- `manual_review_required TINYINT(1) NOT NULL DEFAULT 0`

4. `llm_calls`
- 保留并增强：`trigger_reason`, `schema_valid`, `fallback_used`

建议增量SQL以迁移方式执行（ALTER TABLE），避免重建全库。

---

## 9. 代码实施清单（逐函数级）

## 9.1 `compare_engine.py`

1. `match_objects`
- 新增参数：`source_weights`, `distance_gate`, `confidence_gate`
- 新增输出：`match_confidence`, `match_reason`

2. `run_diff_and_risk`
- 新增参数：`input_policy`, `llm_policy`, `risk_gate`
- 事件生成后调用 `apply_risk_gate(event)`
- 满足触发条件时调用 `llm_review_hook(event)`

3. 新增函数
- `compute_match_score(...)`
- `should_trigger_llm_review(event, policies)`
- `apply_risk_gate(event, gate_config)`
- `merge_llm_review(event, llm_output)`

## 9.2 新增模块建议

- `input_normalizer.py`：DWG/PDF标准化
- `pdf_parser_adapter.py`：PDF解析适配
- `llm_reviewer.py`：LLM调用、schema校验、回退
- `policy_loader.py`：配置加载与校验

---

## 10. 实施排期（建议4周）

### 第1周：输入与数据结构
- 完成配置模型
- 完成中间对象schema
- 完成MySQL增量字段迁移

### 第2周：算法与门控
- 完成匹配评分改造
- 完成风险门控逻辑
- 输出可疑样本队列

### 第3周：LLM复核接入
- 接入网关
- 完成schema校验与回退机制
- 完成审计字段落库

### 第4周：联调与验收
- DWG-only回归
- DWG+PDF+LLM联调
- 指标评估与阈值校准

---

## 11. 验收标准

1. DWG-only结果准确性不低于现有版本
2. PDF参与时，语义误判率下降（对照基线）
3. high/critical 事件100%可追溯且人工复核可达
4. 每条LLM增强结果具备证据回指和schema合规记录
5. 配置项可实现：PDF可跳过、LLM可开关、门控可调

---

## 12. 交付物清单

1. 改造后代码（算法层、适配层、LLM复核层）
2. MySQL增量迁移脚本（up/down）
3. 更新后的OpenAPI文档
4. 配置模板（input_policy + llm_policy + risk_gate）
5. 联调报告（准确率、召回率、误报率、复核采纳率）

---

## 13. 风险与缓解

1. PDF质量差导致波动
- 缓解：默认DWG优先，PDF低置信只做疑似

2. LLM输出不稳定
- 缓解：强Schema校验 + 重试 + 回退模板

3. 人工复核压力过大
- 缓解：仅触发可疑样本，逐步调高触发阈值

4. 多源冲突带来解释复杂
- 缓解：统一trace链路，输出证据优先级（DWG > PDF > LLM）

---

## 14. 立即执行建议（下一步）

1. 先落地 **MySQL增量迁移SQL**（新增source/confidence/审核字段）
2. 再改 `compare_engine.py` 三个核心函数（匹配评分、门控、LLM hook）
3. 最后补 API 与联调测试，不先改前端可视化

这样可以在不推翻现有MVP的前提下，最小成本实现“准确率增强版”。
