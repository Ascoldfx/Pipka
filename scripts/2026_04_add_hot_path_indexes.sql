-- Hot-path indexes for dashboard queries.
-- Safe to apply online: CREATE INDEX IF NOT EXISTS + CONCURRENTLY wherever supported.
-- For existing production (Postgres), run:
--   docker compose exec -T db psql -U jobhunt -d jobhunt < scripts/2026_04_add_hot_path_indexes.sql
-- For fresh SQLite dev DBs the same statements work (minus CONCURRENTLY).

-- jobs: listing sort + common filters
CREATE INDEX IF NOT EXISTS ix_jobs_posted_at_desc ON jobs (posted_at);
CREATE INDEX IF NOT EXISTS ix_jobs_source         ON jobs (source);
CREATE INDEX IF NOT EXISTS ix_jobs_country        ON jobs (country);

-- job_scores: LEFT JOIN in get_jobs + per-user stats aggregates
CREATE INDEX IF NOT EXISTS ix_job_scores_user_job   ON job_scores (user_id, job_id);
CREATE INDEX IF NOT EXISTS ix_job_scores_user_score ON job_scores (user_id, score);

-- applications: LEFT JOIN in get_jobs + status filter + recent-activity queries
CREATE INDEX IF NOT EXISTS ix_applications_user_job    ON applications (user_id, job_id);
CREATE INDEX IF NOT EXISTS ix_applications_user_status ON applications (user_id, status);
CREATE INDEX IF NOT EXISTS ix_applications_updated_at  ON applications (updated_at);
