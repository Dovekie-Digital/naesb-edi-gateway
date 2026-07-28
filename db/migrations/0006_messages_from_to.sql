-- The messages table only ever recorded partner_name (the resolved trading
-- partner config name), never the literal from/to DUNS values carried on
-- the wire in the NAESB envelope (app/envelope/fields.py's EnvelopeFields,
-- already stored on outbound_jobs via 0003_outbound_jobs.sql). Adding them
-- here gives an audit trail of the actual origin/destination presented on
-- each message, inbound and outbound, independent of partner_name.

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS from_id  text,
    ADD COLUMN IF NOT EXISTS to_id    text;

CREATE INDEX IF NOT EXISTS idx_messages_from_id ON messages (from_id);
CREATE INDEX IF NOT EXISTS idx_messages_to_id   ON messages (to_id);
