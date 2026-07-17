-- Initialization script run when the pgvector container is first started.
-- Creates the vector extension and a non-superuser with full permissions on
-- the collabv database.
CREATE EXTENSION IF NOT EXISTS vector;

-- The collabv user is already the DB owner; this just ensures the extension
-- is available before alembic migrations run.
GRANT ALL PRIVILEGES ON DATABASE collabv TO collabv;
