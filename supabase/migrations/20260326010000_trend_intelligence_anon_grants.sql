-- Grant read/write access on Trend Intelligence tables to the anon and
-- authenticated roles so the Streamlit app (which uses the public/anon key)
-- can insert scan runs, topic results, and saved candidates.
--
-- Apply in Supabase SQL Editor if the migration runner is not configured.

grant select, insert, update, delete
    on public.trend_scan_runs
    to anon, authenticated;

grant select, insert, update, delete
    on public.trend_topic_results
    to anon, authenticated;

grant select, insert, update, delete
    on public.saved_topic_candidates
    to anon, authenticated;

grant select, insert, update, delete
    on public.trend_topic_script_jobs
    to anon, authenticated;
