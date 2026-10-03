-- Explicit setup only. Runtime admission never creates or alters tables.
CREATE TABLE IF NOT EXISTS public.ai_request_quota_v1 (
    scope TEXT NOT NULL CHECK (scope ~ '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$'),
    resource TEXT NOT NULL CHECK (resource IN ('model', 'sandbox')),
    minute_start BIGINT NOT NULL DEFAULT 0 CHECK (minute_start >= 0),
    day_start BIGINT NOT NULL DEFAULT 0 CHECK (day_start >= 0),
    minute_count INTEGER NOT NULL DEFAULT 0 CHECK (minute_count >= 0),
    day_count INTEGER NOT NULL DEFAULT 0 CHECK (day_count >= 0),
    minute_limit INTEGER NOT NULL CHECK (minute_limit BETWEEN 1 AND 1000000),
    day_limit INTEGER NOT NULL CHECK (day_limit BETWEEN 1 AND 1000000),
    PRIMARY KEY (scope, resource),
    CHECK (minute_count <= minute_limit AND day_count <= day_limit)
);
