CREATE TABLE engine_checkpoints (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
