-- Fix double-encoded JSON list columns (MySQL 8+).
--
-- Background: before the write-path fix, BaseUpsertDAO._derived_row (and the
-- incidentio single-row methods) ran json.dumps() on list columns before handing
-- them to SQLAlchemy, whose JSON column type then encoded them AGAIN. The DB
-- therefore stores a JSON *string* like
--     "[\"sre-kpi-alerts\", \"mussel\", \"silla\"]"
-- instead of a JSON *array*
--     ["sre-kpi-alerts", "mussel", "silla"]
--
-- This script rewrites every corrupted cell to the correct array. It is:
--   * Targeted   — only cells whose JSON_TYPE is STRING (i.e. double-encoded) and
--                  whose inner text is a valid JSON array are touched.
--   * Idempotent — already-correct ARRAY cells and NULLs are left untouched, so
--                  it is safe to re-run.
--   * Safe       — a cell whose inner text is NOT a valid JSON array is skipped
--                  (never corrupted further), so anomalies are left for review.
--
-- Affected columns (all MySQL JSON type, list[str] | None):
--   ghe_pull_requests   : services
--   jira_issues         : services, tcmr_related_services
--   incidentio_incidents: impacted_parties, impacted_core_functions,
--                         affected_services, detection_methods
--
-- HOW TO RUN
--   1. Back up the affected tables first (see the mysqldump note at the bottom).
--   2. Run the "DRY RUN" SELECTs to see how many rows will change per column.
--   3. Run inside the transaction block. Review, then COMMIT (or ROLLBACK).
--
-- The predicate used for every column:
--   JSON_TYPE(col) = 'STRING'                    -- it is double-encoded, and
--   AND JSON_VALID(JSON_UNQUOTE(col))            -- the inner text is valid JSON, and
--   AND JSON_TYPE(JSON_UNQUOTE(col)) = 'ARRAY'   -- specifically a JSON array.
-- The fix value:
--   CAST(JSON_UNQUOTE(col) AS JSON)              -- parse the inner JSON once.

-- ---------------------------------------------------------------------------
-- DRY RUN — count corrupted rows per column (run before the UPDATEs).
-- ---------------------------------------------------------------------------
-- SELECT 'ghe_pull_requests.services' AS col, COUNT(*) AS corrupted_rows
--   FROM ghe_pull_requests
--  WHERE JSON_TYPE(services) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(services))
--    AND JSON_TYPE(JSON_UNQUOTE(services)) = 'ARRAY'
-- UNION ALL
-- SELECT 'jira_issues.services', COUNT(*)
--   FROM jira_issues
--  WHERE JSON_TYPE(services) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(services))
--    AND JSON_TYPE(JSON_UNQUOTE(services)) = 'ARRAY'
-- UNION ALL
-- SELECT 'jira_issues.tcmr_related_services', COUNT(*)
--   FROM jira_issues
--  WHERE JSON_TYPE(tcmr_related_services) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(tcmr_related_services))
--    AND JSON_TYPE(JSON_UNQUOTE(tcmr_related_services)) = 'ARRAY'
-- UNION ALL
-- SELECT 'incidentio_incidents.impacted_parties', COUNT(*)
--   FROM incidentio_incidents
--  WHERE JSON_TYPE(impacted_parties) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(impacted_parties))
--    AND JSON_TYPE(JSON_UNQUOTE(impacted_parties)) = 'ARRAY'
-- UNION ALL
-- SELECT 'incidentio_incidents.impacted_core_functions', COUNT(*)
--   FROM incidentio_incidents
--  WHERE JSON_TYPE(impacted_core_functions) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(impacted_core_functions))
--    AND JSON_TYPE(JSON_UNQUOTE(impacted_core_functions)) = 'ARRAY'
-- UNION ALL
-- SELECT 'incidentio_incidents.affected_services', COUNT(*)
--   FROM incidentio_incidents
--  WHERE JSON_TYPE(affected_services) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(affected_services))
--    AND JSON_TYPE(JSON_UNQUOTE(affected_services)) = 'ARRAY'
-- UNION ALL
-- SELECT 'incidentio_incidents.detection_methods', COUNT(*)
--   FROM incidentio_incidents
--  WHERE JSON_TYPE(detection_methods) = 'STRING'
--    AND JSON_VALID(JSON_UNQUOTE(detection_methods))
--    AND JSON_TYPE(JSON_UNQUOTE(detection_methods)) = 'ARRAY';

-- ---------------------------------------------------------------------------
-- FIX — wrapped in a transaction. Review the row counts, then COMMIT.
-- ---------------------------------------------------------------------------
START TRANSACTION;

-- ghe_pull_requests.services
UPDATE ghe_pull_requests
   SET services = CAST(JSON_UNQUOTE(services) AS JSON)
 WHERE JSON_TYPE(services) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(services))
   AND JSON_TYPE(JSON_UNQUOTE(services)) = 'ARRAY';

-- jira_issues.services
UPDATE jira_issues
   SET services = CAST(JSON_UNQUOTE(services) AS JSON)
 WHERE JSON_TYPE(services) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(services))
   AND JSON_TYPE(JSON_UNQUOTE(services)) = 'ARRAY';

-- jira_issues.tcmr_related_services
UPDATE jira_issues
   SET tcmr_related_services = CAST(JSON_UNQUOTE(tcmr_related_services) AS JSON)
 WHERE JSON_TYPE(tcmr_related_services) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(tcmr_related_services))
   AND JSON_TYPE(JSON_UNQUOTE(tcmr_related_services)) = 'ARRAY';

-- incidentio_incidents.impacted_parties
UPDATE incidentio_incidents
   SET impacted_parties = CAST(JSON_UNQUOTE(impacted_parties) AS JSON)
 WHERE JSON_TYPE(impacted_parties) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(impacted_parties))
   AND JSON_TYPE(JSON_UNQUOTE(impacted_parties)) = 'ARRAY';

-- incidentio_incidents.impacted_core_functions
UPDATE incidentio_incidents
   SET impacted_core_functions = CAST(JSON_UNQUOTE(impacted_core_functions) AS JSON)
 WHERE JSON_TYPE(impacted_core_functions) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(impacted_core_functions))
   AND JSON_TYPE(JSON_UNQUOTE(impacted_core_functions)) = 'ARRAY';

-- incidentio_incidents.affected_services
UPDATE incidentio_incidents
   SET affected_services = CAST(JSON_UNQUOTE(affected_services) AS JSON)
 WHERE JSON_TYPE(affected_services) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(affected_services))
   AND JSON_TYPE(JSON_UNQUOTE(affected_services)) = 'ARRAY';

-- incidentio_incidents.detection_methods
UPDATE incidentio_incidents
   SET detection_methods = CAST(JSON_UNQUOTE(detection_methods) AS JSON)
 WHERE JSON_TYPE(detection_methods) = 'STRING'
   AND JSON_VALID(JSON_UNQUOTE(detection_methods))
   AND JSON_TYPE(JSON_UNQUOTE(detection_methods)) = 'ARRAY';

-- Review the per-statement "Rows matched / Changed" output above.
-- If it looks right:
COMMIT;
-- Otherwise:
-- ROLLBACK;

-- ---------------------------------------------------------------------------
-- POST-FIX VERIFICATION — every count below should be 0 (no STRING-typed cells
-- left). Run after COMMIT.
-- ---------------------------------------------------------------------------
-- SELECT 'ghe_pull_requests.services' AS col,
--        SUM(JSON_TYPE(services) = 'STRING') AS still_double_encoded
--   FROM ghe_pull_requests
-- UNION ALL
-- SELECT 'jira_issues.services',
--        SUM(JSON_TYPE(services) = 'STRING') FROM jira_issues
-- UNION ALL
-- SELECT 'jira_issues.tcmr_related_services',
--        SUM(JSON_TYPE(tcmr_related_services) = 'STRING') FROM jira_issues
-- UNION ALL
-- SELECT 'incidentio_incidents.impacted_parties',
--        SUM(JSON_TYPE(impacted_parties) = 'STRING') FROM incidentio_incidents
-- UNION ALL
-- SELECT 'incidentio_incidents.impacted_core_functions',
--        SUM(JSON_TYPE(impacted_core_functions) = 'STRING') FROM incidentio_incidents
-- UNION ALL
-- SELECT 'incidentio_incidents.affected_services',
--        SUM(JSON_TYPE(affected_services) = 'STRING') FROM incidentio_incidents
-- UNION ALL
-- SELECT 'incidentio_incidents.detection_methods',
--        SUM(JSON_TYPE(detection_methods) = 'STRING') FROM incidentio_incidents;

-- ---------------------------------------------------------------------------
-- BACKUP (run in a shell BEFORE this script; not SQL):
--   mysqldump <db> ghe_pull_requests jira_issues incidentio_incidents \
--     > double_encode_backup_$(date +%Y%m%d_%H%M%S).sql
-- ---------------------------------------------------------------------------
