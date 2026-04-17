-- migration_alter_v2_down_mysql.sql
-- 回滚 v2 增量字段

USE cad_compare;

ALTER TABLE llm_calls
  DROP COLUMN fallback_used,
  DROP COLUMN schema_valid,
  DROP COLUMN trigger_reason;

ALTER TABLE risk_events
  DROP COLUMN manual_review_required,
  DROP COLUMN review_source,
  DROP COLUMN llm_reviewed;

ALTER TABLE diff_results
  DROP COLUMN manual_review_required,
  DROP COLUMN source_type,
  DROP COLUMN match_confidence;

ALTER TABLE design_objects
  DROP COLUMN trace_json,
  DROP COLUMN source_confidence,
  DROP COLUMN source_type;
