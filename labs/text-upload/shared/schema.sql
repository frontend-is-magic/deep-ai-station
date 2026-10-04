CREATE TABLE storage_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  storage_contract TEXT NOT NULL CHECK (
    typeof(storage_contract) = 'text' AND storage_contract = 'text-upload-sqlite-v1'
  ),
  next_id INTEGER NOT NULL CHECK (
    typeof(next_id) = 'integer' AND next_id BETWEEN 1 AND 1000000
  )
);
CREATE TABLE documents (
  id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 999999),
  owner_id TEXT NOT NULL CHECK (
    typeof(owner_id) = 'text' AND owner_id IN ('alice', 'bob')
  ),
  filename TEXT NOT NULL CHECK (
    typeof(filename) = 'text' AND length(filename) BETWEEN 5 AND 64
  ),
  media_type TEXT NOT NULL CHECK (
    typeof(media_type) = 'text' AND media_type IN ('text/plain', 'text/markdown')
  ),
  size_bytes INTEGER NOT NULL CHECK (
    typeof(size_bytes) = 'integer' AND size_bytes BETWEEN 1 AND 4096
  ),
  sha256 TEXT NOT NULL CHECK (
    typeof(sha256) = 'text' AND length(sha256) = 64
    AND sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  content BLOB NOT NULL CHECK (
    typeof(content) = 'blob' AND length(content) = size_bytes
  )
);
CREATE INDEX documents_owner_id_id ON documents(owner_id, id);
INSERT INTO storage_meta(singleton, storage_contract, next_id)
VALUES (1, 'text-upload-sqlite-v1', 1);
PRAGMA application_id = 1146442545;
PRAGMA user_version = 1;
