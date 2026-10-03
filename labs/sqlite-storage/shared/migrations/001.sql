CREATE TABLE progress (
  id INTEGER PRIMARY KEY,
  owner_id TEXT NOT NULL,
  lesson_id TEXT NOT NULL,
  completed INTEGER NOT NULL CHECK (completed IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE (owner_id, lesson_id)
);
CREATE TABLE audit (
  id INTEGER PRIMARY KEY,
  progress_id INTEGER NOT NULL REFERENCES progress(id),
  completed INTEGER NOT NULL CHECK (completed IN (0, 1)),
  at TEXT NOT NULL
);
CREATE INDEX audit_progress_id ON audit(progress_id, id);
