ALTER TABLE messages ADD COLUMN IF NOT EXISTS processed_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_messages_status ON messages (status);
