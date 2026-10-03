ALTER TABLE progress ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
UPDATE progress SET updated_at = created_at;
