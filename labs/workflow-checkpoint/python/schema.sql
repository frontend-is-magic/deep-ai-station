CREATE TABLE metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    workflow_version TEXT NOT NULL,
    corpus_revision TEXT NOT NULL,
    corpus_sha256 TEXT NOT NULL
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    case_id TEXT NOT NULL CHECK (case_id IN ('normal', 'empty', 'conflict')),
    workflow_version TEXT NOT NULL,
    corpus_revision TEXT NOT NULL,
    corpus_sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision BETWEEN 0 AND 3),
    phase TEXT NOT NULL CHECK (
        (revision = 0 AND phase = 'ready') OR
        (revision = 1 AND phase = 'retrieved') OR
        (revision = 2 AND phase = 'drafted') OR
        (revision = 3 AND phase = 'completed')
    ),
    outcome TEXT CHECK (
        (revision < 3 AND outcome IS NULL) OR
        (revision = 3 AND outcome IS NOT NULL AND outcome IN ('complete', 'insufficient_evidence', 'conflicting_evidence'))
    ),
    last_hash TEXT CHECK (
        (revision = 0 AND last_hash IS NULL) OR
        (revision > 0 AND last_hash IS NOT NULL AND length(last_hash) = 64 AND last_hash NOT GLOB '*[^0-9a-f]*')
    )
);
CREATE TABLE checkpoints (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision BETWEEN 1 AND 3),
    node TEXT NOT NULL CHECK (
        (revision = 1 AND node = 'retrieve') OR
        (revision = 2 AND node = 'draft') OR
        (revision = 3 AND node = 'validate')
    ),
    output_json TEXT NOT NULL CHECK (length(CAST(output_json AS BLOB)) <= 16384),
    previous_hash TEXT CHECK (
        (revision = 1 AND previous_hash IS NULL) OR
        (revision > 1 AND previous_hash IS NOT NULL AND length(previous_hash) = 64 AND previous_hash NOT GLOB '*[^0-9a-f]*')
    ),
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64 AND state_hash NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (run_id, revision),
    UNIQUE (run_id, node)
);
