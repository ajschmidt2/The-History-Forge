"""Supabase connection diagnostics — rendered as a tab inside app.py."""
import traceback

import streamlit as st

from src.config import get_secret


def tab_supabase_diagnostics() -> None:
    st.subheader("🔌 Supabase Connection")
    st.caption("Verify that the app can read from and write to Supabase cloud storage.")

    # ------------------------------------------------------------------
    # 1. Credentials
    # ------------------------------------------------------------------
    st.markdown("#### 1. Credentials")

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
            'SUPABASE_URL = "https://<ref>.supabase.co"\n'
            'SUPABASE_KEY = "<your-anon-key>"\n'
            "```\n\n"
            "See **SUPABASE_SETUP.md** for the full setup guide."
        )
        return

    # ------------------------------------------------------------------
    # 2. Client initialisation
    # ------------------------------------------------------------------
    st.markdown("#### 2. Client")
    try:
        from supabase import create_client  # type: ignore

        sb = create_client(url, key)
        st.success("Supabase client created successfully.")
    except Exception as exc:
        st.error(f"Failed to create Supabase client: {exc}")
        st.code(traceback.format_exc())
        return

    # ------------------------------------------------------------------
    # 3. Database read
    # ------------------------------------------------------------------
    st.markdown("#### 3. Database Read")
    try:
        resp = sb.table("projects").select("id,title,created_at").limit(5).execute()
        rows = resp.data or []
        st.success(f"Read from `projects` table succeeded — {len(rows)} row(s) returned.")
        if rows:
            st.dataframe(rows, hide_index=True)
        else:
            st.info("The `projects` table exists but is empty.")
    except Exception as exc:
        st.error(f"Read failed: {exc}")
        st.code(traceback.format_exc())
        st.warning(
            "Make sure you have run the SQL migrations in `SUPABASE_SETUP.md` "
            "to create the `projects` and `assets` tables."
        )

    # ------------------------------------------------------------------
    # 4. Write test (button-gated so it doesn't run on every render)
    # ------------------------------------------------------------------
    st.markdown("#### 4. Write Test")
    st.caption("Upserts a temporary row into `projects` then deletes it.")

    if st.button("Run Write Test", key="diag_write_test", type="primary"):
        TEST_ID = "__diagnostics_test__"
        try:
            sb.table("projects").upsert(
                {"id": TEST_ID, "title": "Diagnostics write test"},
                on_conflict="id",
            ).execute()
            st.success("Upsert succeeded.")

            check = sb.table("projects").select("id,title").eq("id", TEST_ID).execute()
            if check.data:
                st.success(f"Read-back confirmed: {check.data[0]}")
            else:
                st.warning("Upsert reported success but row was not found on read-back.")

            sb.table("projects").delete().eq("id", TEST_ID).execute()
            st.success("Test row deleted — write test passed.")
        except Exception as exc:
            st.error(f"Write test failed: {exc}")
            st.code(traceback.format_exc())
            st.warning(
                "Common causes:\n"
                "- `projects` table missing — run migrations from SUPABASE_SETUP.md\n"
                "- RLS blocking the anon key — add an insert/delete policy\n"
                "- Wrong URL or key"
            )

    # ------------------------------------------------------------------
    # 5. Storage buckets
    # ------------------------------------------------------------------
    st.markdown("#### 5. Storage Buckets")

    EXPECTED_BUCKETS = [
        "history-forge-images",
        "history-forge-audio",
        "history-forge-videos",
    ]
    try:
        buckets_resp = sb.storage.list_buckets()
        existing = {b.name for b in buckets_resp} if buckets_resp else set()
        for name in EXPECTED_BUCKETS:
            if name in existing:
                st.success(f"Bucket `{name}` — found.")
            else:
                st.warning(
                    f"Bucket `{name}` not found. "
                    "Create it in the Supabase dashboard under **Storage**."
                )
    except Exception as exc:
        st.error(f"Could not list storage buckets: {exc}")
        st.caption("This may be normal if the anon key lacks `storage.read` permission.")
