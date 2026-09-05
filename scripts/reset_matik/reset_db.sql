-- reset_db.sql — wipe all data and reset tracker state without dropping the database
-- Safe to run repeatedly. Does not alter schema or touch alembic_version.
-- Usage: mysql -h <host> -u <user> -p <db> < reset_db.sql

SET FOREIGN_KEY_CHECKS = 0;

-- -----------------------------------------------------------------------
-- Data tables — truncate resets auto-increment counters
-- -----------------------------------------------------------------------
TRUNCATE TABLE reliability_correlations;
TRUNCATE TABLE reliability_correlation_groups;
TRUNCATE TABLE ghe_pull_requests;
TRUNCATE TABLE ghe_pr_tracker;
TRUNCATE TABLE ghe_repositories;
TRUNCATE TABLE ghe_organizations;
TRUNCATE TABLE greenroom_entities;
TRUNCATE TABLE incidentio_incidents;
TRUNCATE TABLE jira_issues;

-- -----------------------------------------------------------------------
-- Trackers — delete and re-seed to their initial migration state
-- -----------------------------------------------------------------------

-- incidentio_tracker: single row, reset to initial sync not started
DELETE FROM incidentio_tracker;
INSERT INTO incidentio_tracker (id, timestamp, status, initial_sync_complete, last_updated_at_cursor)
VALUES (1, NOW(), 'OK', FALSE, NULL);

-- jira_batch_tracker: reset both ticket types to the initial batch window
DELETE FROM jira_batch_tracker;
INSERT INTO jira_batch_tracker (ticket_type, batch_start, batch_end, window_days, status, updated_at)
VALUES
    ('tcmr',        '2025-01-01 00:00:00', '2025-01-15 00:00:00', 14, NULL, NOW()),
    ('operational', '2025-01-01 00:00:00', '2025-01-15 00:00:00', 14, NULL, NOW());

SET FOREIGN_KEY_CHECKS = 1;
