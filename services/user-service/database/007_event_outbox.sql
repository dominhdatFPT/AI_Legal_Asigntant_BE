CREATE TABLE IF NOT EXISTS public.event_outbox (
  id varchar(64) PRIMARY KEY, routing_key varchar(100) NOT NULL, payload_json text NOT NULL,
  attempts integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
  available_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz);
CREATE INDEX IF NOT EXISTS event_outbox_due ON public.event_outbox (available_at) WHERE published_at IS NULL;
ALTER TABLE public.event_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.event_outbox FROM anon, authenticated;
