-- Executed in the same explicit initialization transaction as fixtures and user_version.
CREATE TABLE documents (
    owner_id TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 2000 AND instr(content, char(0)) = 0),
    version INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version BETWEEN 1 AND 2147483647),
    PRIMARY KEY (owner_id, id)
);
CREATE TABLE operations (
    owner_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    tool TEXT NOT NULL CHECK (tool = 'publish_revision'),
    document_id TEXT NOT NULL,
    expected_version INTEGER NOT NULL CHECK (typeof(expected_version) = 'integer' AND expected_version BETWEEN 1 AND 2147483647),
    before_content TEXT NOT NULL,
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 2000 AND instr(content, char(0)) = 0),
    intent_hash TEXT NOT NULL CHECK (length(intent_hash) = 64 AND intent_hash NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL CHECK (status IN ('prepared','approved','applied','revoked','expired')),
    prepared_at REAL NOT NULL CHECK (prepared_at BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308),
    approved_at REAL,
    expires_at REAL,
    PRIMARY KEY (owner_id, operation_id),
    FOREIGN KEY (owner_id, document_id) REFERENCES documents(owner_id, id),
    CHECK ((approved_at IS NULL AND expires_at IS NULL) OR
        (approved_at IS NOT NULL AND expires_at IS NOT NULL AND approved_at BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308 AND
         expires_at BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308 AND expires_at > approved_at)),
    CHECK ((status = 'prepared' AND approved_at IS NULL) OR status = 'revoked' OR
        (status IN ('approved','applied','expired') AND approved_at IS NOT NULL AND expires_at IS NOT NULL))
);
CREATE TABLE publications (
    owner_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version BETWEEN 2 AND 2147483647),
    content TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    applied_at REAL NOT NULL CHECK (applied_at BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308),
    PRIMARY KEY (owner_id, operation_id),
    UNIQUE (owner_id, document_id, version),
    FOREIGN KEY (owner_id, operation_id) REFERENCES operations(owner_id, operation_id),
    FOREIGN KEY (owner_id, document_id) REFERENCES documents(owner_id, id)
);
CREATE TRIGGER immutable_intent BEFORE UPDATE ON operations
WHEN OLD.owner_id IS NOT NEW.owner_id OR OLD.operation_id IS NOT NEW.operation_id OR
     OLD.requester_id IS NOT NEW.requester_id OR OLD.tool IS NOT NEW.tool OR
     OLD.document_id IS NOT NEW.document_id OR OLD.expected_version IS NOT NEW.expected_version OR
     OLD.before_content IS NOT NEW.before_content OR OLD.content IS NOT NEW.content OR
     OLD.intent_hash IS NOT NEW.intent_hash OR OLD.prepared_at IS NOT NEW.prepared_at
BEGIN SELECT RAISE(ABORT, 'immutable_intent'); END;
CREATE TRIGGER operation_transition BEFORE UPDATE ON operations
WHEN NOT (
    (OLD.status = NEW.status AND OLD.approved_at IS NEW.approved_at AND OLD.expires_at IS NEW.expires_at) OR
    (OLD.status = 'prepared' AND NEW.status = 'approved') OR
    (OLD.status IN ('prepared','approved') AND NEW.status = 'revoked' AND
        OLD.approved_at IS NEW.approved_at AND OLD.expires_at IS NEW.expires_at) OR
    (OLD.status = 'approved' AND NEW.status IN ('expired','applied') AND
        OLD.approved_at IS NEW.approved_at AND OLD.expires_at IS NEW.expires_at)
)
BEGIN SELECT RAISE(ABORT, 'invalid_transition'); END;
CREATE TRIGGER publication_matches_intent BEFORE INSERT ON publications
WHEN NOT EXISTS (
    SELECT 1 FROM operations WHERE owner_id = NEW.owner_id AND operation_id = NEW.operation_id
      AND status = 'approved' AND document_id = NEW.document_id AND content = NEW.content
      AND intent_hash = NEW.intent_hash AND expected_version + 1 = NEW.version
      AND EXISTS (SELECT 1 FROM documents WHERE owner_id = NEW.owner_id AND id = NEW.document_id
          AND version = NEW.version AND content = NEW.content)
)
BEGIN SELECT RAISE(ABORT, 'invalid_publication'); END;
CREATE TRIGGER applied_requires_publication BEFORE UPDATE OF status ON operations
WHEN NEW.status = 'applied' AND NOT EXISTS (
    SELECT 1 FROM publications WHERE owner_id = NEW.owner_id AND operation_id = NEW.operation_id
)
BEGIN SELECT RAISE(ABORT, 'missing_publication'); END;
CREATE TRIGGER immutable_publication_update BEFORE UPDATE ON publications
BEGIN SELECT RAISE(ABORT, 'immutable_publication'); END;
CREATE TRIGGER immutable_publication_delete BEFORE DELETE ON publications
BEGIN SELECT RAISE(ABORT, 'immutable_publication'); END;
CREATE TRIGGER immutable_operation_delete BEFORE DELETE ON operations
BEGIN SELECT RAISE(ABORT, 'immutable_operation'); END;
