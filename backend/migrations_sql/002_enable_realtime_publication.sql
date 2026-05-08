-- Enable Supabase realtime for tables the HITL queue page subscribes to.
-- Idempotent: ALTER PUBLICATION ... ADD TABLE errors if already added, so
-- wrap in a DO block and ignore duplicate errors.
DO $$
BEGIN
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE calls;
  EXCEPTION WHEN duplicate_object THEN NULL;
  END;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE claim_locks;
  EXCEPTION WHEN duplicate_object THEN NULL;
  END;
END $$;
