-- migration_alter_v2_up_mysql.sql
-- 在既有MySQL表结构上追加 DWG/PDF/LLM 相关字段

USE cad_compare;

ALTER TABLE design_objects
  ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'dwg' AFTER elevation_m,
  ADD COLUMN source_confidence DECIMAL(5,4) NULL AFTER source_type,
  ADD COLUMN trace_json JSON NULL AFTER source_confidence;

ALTER TABLE diff_results
  ADD COLUMN match_confidence DECIMAL(5,4) NULL AFTER threshold_value,
  ADD COLUMN source_type VARCHAR(16) NULL AFTER match_confidence,
  ADD COLUMN manual_review_required TINYINT(1) NOT NULL DEFAULT 0 AFTER source_type;

ALTER TABLE risk_events
  ADD COLUMN llm_reviewed TINYINT(1) NOT NULL DEFAULT 0 AFTER suggestion,
  ADD COLUMN review_source VARCHAR(32) NULL AFTER llm_reviewed,
  ADD COLUMN manual_review_required TINYINT(1) NOT NULL DEFAULT 0 AFTER review_source;

ALTER TABLE llm_calls
  ADD COLUMN trigger_reason VARCHAR(128) NULL AFTER prompt_template_id,
  ADD COLUMN schema_valid TINYINT(1) NULL AFTER trigger_reason,
  ADD COLUMN fallback_used TINYINT(1) NULL AFTER schema_valid;
