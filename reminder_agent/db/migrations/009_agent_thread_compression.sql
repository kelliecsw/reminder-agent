ALTER TABLE agent_threads
ADD COLUMN context_summary TEXT NOT NULL DEFAULT '';

ALTER TABLE agent_threads
ADD COLUMN compression_step_count INTEGER NOT NULL DEFAULT 0
CHECK (compression_step_count >= 0);
