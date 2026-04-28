USE x106;

ALTER TABLE vibes ADD COLUMN title VARCHAR(255) NOT NULL DEFAULT '' AFTER mood_emoji;

-- Migrate JSON-formatted notes (written by the temporary formatNote helper)
UPDATE vibes
SET
  title = JSON_UNQUOTE(JSON_EXTRACT(note, '$.t')),
  note  = JSON_UNQUOTE(JSON_EXTRACT(note, '$.b'))
WHERE JSON_VALID(note) AND JSON_EXTRACT(note, '$.t') IS NOT NULL;

-- Migrate JSON notes that only have a body and no title
UPDATE vibes
SET note = JSON_UNQUOTE(JSON_EXTRACT(note, '$.b'))
WHERE JSON_VALID(note)
  AND JSON_EXTRACT(note, '$.t') IS NULL
  AND JSON_EXTRACT(note, '$.b') IS NOT NULL;
