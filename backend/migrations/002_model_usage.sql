-- Fixed, additive migration. Runtime requests never create tables.
CREATE TABLE IF NOT EXISTS public.ai_model_usage_v1 (
    scope TEXT NOT NULL CHECK (scope ~ '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$'),
    run_id UUID NOT NULL CHECK (substring(run_id::text, 15, 1) = '4'
        AND substring(run_id::text, 20, 1) IN ('8', '9', 'a', 'b')),
    request_index INTEGER NOT NULL CHECK (request_index BETWEEN 1 AND 3),
    provider TEXT NOT NULL CHECK (provider = 'deepseek'),
    model TEXT NOT NULL CHECK (char_length(model) BETWEEN 1 AND 200),
    status TEXT NOT NULL DEFAULT 'admitted'
        CHECK (status IN ('admitted', 'completed', 'failed', 'cancelled')),
    snapshot_seq BIGINT NOT NULL DEFAULT 0 CHECK (snapshot_seq >= 0),
    prompt_tokens BIGINT CHECK (prompt_tokens BETWEEN 0 AND 100000000),
    completion_tokens BIGINT CHECK (completion_tokens BETWEEN 0 AND 100000000),
    total_tokens BIGINT CHECK (total_tokens BETWEEN 0 AND 100000000),
    usage_complete BOOLEAN NOT NULL DEFAULT FALSE,
    truncated BOOLEAN,
    reason TEXT CHECK (reason IN ('completed', 'upstream_error', 'timeout', 'cancelled', 'ledger_error')),
    admitted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    PRIMARY KEY (scope, run_id, request_index),
    CHECK (NOT usage_complete OR (status = 'completed' AND prompt_tokens IS NOT NULL
        AND completion_tokens IS NOT NULL AND total_tokens IS NOT NULL)),
    CHECK ((status = 'admitted' AND finished_at IS NULL AND reason IS NULL
        AND NOT usage_complete AND truncated IS NULL)
        OR (status <> 'admitted' AND finished_at IS NOT NULL AND reason IS NOT NULL)),
    CHECK (status = 'admitted' OR snapshot_seq > 0)
);
CREATE INDEX IF NOT EXISTS ai_model_usage_v1_scope_admitted_at
    ON public.ai_model_usage_v1 (scope, admitted_at);
