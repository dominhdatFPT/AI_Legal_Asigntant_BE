BEGIN;

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.citizen_profiles ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    public.users,
    public.refresh_tokens,
    public.citizen_profiles
FROM PUBLIC, anon, authenticated;

COMMIT;
