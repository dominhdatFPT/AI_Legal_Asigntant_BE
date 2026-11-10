-- IRREVERSIBLE: drops custom forms and submitted values stored by feature 008.
-- Export/back up those rows first if they need to be kept, then run on the Procedure database.
BEGIN;
DROP TABLE IF EXISTS public.custom_form_submissions;
DROP TABLE IF EXISTS public.custom_form_templates;
COMMIT;
