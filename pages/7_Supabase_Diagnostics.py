"""Supabase connection diagnostics page.
Lets you verify that the app can read from and write to Supabase so you can
quickly confirm cloud storage is working before a production run.
"""
import traceback
import streamlit as st
from src.config import get_secret

st.set_page_config(page_title="Supabase Diagnostics", page_icon="🔌")
st.title("🔌 Supabase Connection Diagnostics")
st.caption("Use this page to confirm that the app is connected to Supabase and can write data.")

# ---------------------------------------------------------------------------
# 1. Config check — are the credentials set at all?
# ---------------------------------------------------------------------------
st.subheader("1. Credentials")

url = get_secret("SUPABASE_URL").strip()
key = get_secret("SUPABASE_KEY").strip()

placeholder_urls = {"", "https://xxxxxxxxxxxx.supabase.co"}
placeholder_keys = {"", "your-anon-public-key", "your-anon-key-here"}

url_ok = bool(url) and url not in placeholder_urls
key_ok = bool(key) and key not in placeholder_keys

if url_ok:
    st.success(f"SUPABASE_URL: `{url[:40]}{'...' if len(url) > 40 else ''}`")
else:
    st.error("SUPABASE_URL is missing or still set to a placeholder value.")

if key_ok:
    masked = key[:6] + "..." + key[-4:]
    st.success(f"SUPABASE_KEY: `{masked}` ({len(key)} chars)")
else:
    st.error("SUPABASE_KEY is missing or still set to a placeholder value.")

if not (url_ok and key_ok):
    st.info(
        "Add your Supabase credentials to `.streamlit/secrets.toml`:\n"
        "```toml\n"
        "SUPABASE_URL = \"https://<ref>.supabase.co\"\n"
        "SUPABASE_KEY = \"<your-anon-key>\"\n"
        "```"
    )
    st.stop()

# ---------------------------------------------------------------------------
# 2. Client initialisation
# ---------------------------------------------------------------------------
st.subheader("2. Client Initialisation")
try:
    from supabase import create_client
    sb = create_client(url, key)
    st.success("Supabase client created successfully.")
except Exception as exc:
    st.error(f"Failed to create Supabase client: {exc}")
    st.code(traceback.format_exc())
    st.stop()

# ---------------------------------------------------------------------------
# 3. Database read test
# ---------------------------------------------------------------------------
st.subheader("3. Database Read")
try:
    resp = sb.table("projects").select("id,title,created_at").limit(5).execute()
    rows = resp.data or []
    st.success(f"Read from `projects` table succeeded. Rows returned: {len(rows)}")
    if rows:
        st.dataframe(rows)
    else:
        st.info("The `projects` table exists but is empty.")
except Exception as exc:
    st.error(f"Read failed: {exc}")
    st.code(traceback.format_exc())
    st.warning(
        "Make sure you have run the SQL migrations in `SUPABASE_SETUP.md` to create "
        "the `projects` and `assets` tables."
    )

# ---------------------------------------------------------------------------
# 4. Write test (on button click so it doesn't run on every page load)
# ---------------------------------------------------------------------------
st.subheader("4. Database Write")
st.caption("This will upsert a test row into the `projects` table and then delete it.")

if st.button("Run Write Test", type="primary"):
    TEST_ID = "__diagnostics_test__"
    try:
        # Write
        sb.table("projects").upsert(
            {"id": TEST_ID, "title": "Diagnostics write test"},
            on_conflict="id",
        ).execute()
        st.success("Upsert to `projects` succeeded.")

        # Read it back
        check = sb.table("projects").select("id,title").eq("id", TEST_ID).execute()
        if check.data:
            st.success(f"Read-back confirmed: {check.data[0]}")
        else:
            st.warning("Upsert reported success but row was not found on read-back.")

        # Clean up
        sb.table("projects").delete().eq("id", TEST_ID).execute()
        st.success("Test row deleted. Write test complete — Supabase is working correctly!")
    except Exception as exc:
        st.error(f"Write test failed: {exc}")
        st.code(traceback.format_exc())
        st.warning(
            "Common causes:\n"
            "- The `projects` table doesn't exist (run SQL migrations from SUPABASE_SETUP.md)\n"
            "- Row Level Security (RLS) is blocking the anon key — add an insert/delete policy\n"
            "- Wrong Supabase project URL or key"
        )

# ---------------------------------------------------------------------------
# 5. Storage bucket check
# ---------------------------------------------------------------------------
st.subheader("5. Storage Buckets")

EXPECTED_BUCKETS = ["history-forge-images", "history-forge-audio", "history-forge-videos"]
try:
    buckets_resp = sb.storage.list_buckets()
    existing = {b.name for b in buckets_resp} if buckets_resp else set()
    for name in EXPECTED_BUCKETS:
        if name in existing:
            st.success(f"Bucket `{name}` exists.")
        else:
            st.warning(f"Bucket `{name}` NOT found. Create it in the Supabase dashboard under Storage.")
except Exception as exc:
    st.error(f"Could not list storage buckets: {exc}")
    st.caption("This may be normal if the anon key lacks storage.read permissions.")
