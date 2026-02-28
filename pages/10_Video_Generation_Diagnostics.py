"""Video generation diagnostic page — Google Veo and OpenAI Sora.

Run this page to pinpoint exactly why a video generation call is failing.
It walks through every layer: credentials → network → provider API → edge
function internals, and shows actionable fix instructions at each step.

The page is structured in two independent sections so you can diagnose
either provider on its own.
"""
from __future__ import annotations

import os
import traceback

import requests
import streamlit as st

from src.config import get_secret

st.set_page_config(page_title="Video Generation Diagnostics", page_icon="🎬")
st.title("🎬 Video Generation Diagnostics")
st.caption(
    "Diagnose Google Veo and OpenAI Sora video generation end-to-end. "
    "Each check tells you exactly what is wrong and how to fix it."
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_URLS = {"", "https://xxxxxxxxxxxx.supabase.co"}
_PLACEHOLDER_KEYS = {"", "your-anon-public-key", "your-anon-key-here"}


def _result_row(
    results: list[tuple[str, bool, str]],
    label: str,
    passed: bool,
    detail: str,
) -> None:
    results.append((label, passed, detail))


def _render_results(results: list[tuple[str, bool, str]], *, section: str) -> bool:
    """Render a list of check results and return True if all passed."""
    all_passed = all(ok for _, ok, _ in results)
    if all_passed:
        st.success(f"All {section} checks passed.")
    else:
        first_fail = next((label for label, ok, _ in results if not ok), None)
        st.error(f"First failure: **{first_fail}**")

    for label, passed, detail in results:
        icon = "✅" if passed else "❌"
        with st.expander(f"{icon} {label}", expanded=not passed):
            st.write(detail)
    return all_passed


# ===========================================================================
# SECTION 1 — Google Veo
# ===========================================================================

st.divider()
st.header("🔷 Google Veo (via Supabase Edge Function)")
st.markdown(
    "Veo video generation is **proxied through a Supabase Edge Function** "
    "(`veo-generate`). A `500 Internal Server Error` from that URL means the "
    "function is reachable but crashed internally — usually because one of the "
    "**Google Cloud secrets** is missing or mis-configured inside Supabase."
)

if st.button("Run Veo Diagnostics", type="primary", key="veo_run"):
    with st.spinner("Running Veo checks…"):
        try:
            results: list[tuple[str, bool, str]] = []

            # ------------------------------------------------------------------
            # 1. SUPABASE_URL
            # ------------------------------------------------------------------
            supabase_url = get_secret("SUPABASE_URL", "").strip()
            url_ok = bool(supabase_url) and supabase_url not in _PLACEHOLDER_URLS
            _result_row(
                results,
                "SUPABASE_URL is configured",
                url_ok,
                f"`{supabase_url[:50]}`" if url_ok else (
                    "SUPABASE_URL is missing or still set to a placeholder.\n\n"
                    "**Fix:** Add it to `.streamlit/secrets.toml`:\n"
                    "```toml\nSUPABASE_URL = \"https://<ref>.supabase.co\"\n```"
                ),
            )

            # ------------------------------------------------------------------
            # 2. Supabase invoke key
            # ------------------------------------------------------------------
            supabase_key = (
                get_secret("SUPABASE_KEY", "").strip()
                or get_secret("SUPABASE_ANON_KEY", "").strip()
                or get_secret("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            )
            key_ok = bool(supabase_key) and supabase_key not in _PLACEHOLDER_KEYS
            _result_row(
                results,
                "Supabase invoke key is configured (SUPABASE_KEY / SUPABASE_ANON_KEY)",
                key_ok,
                (
                    f"Key resolved — `{supabase_key[:6]}…{supabase_key[-4:]}` "
                    f"({len(supabase_key)} chars)"
                    if key_ok
                    else (
                        "No valid Supabase key found.  Checked: SUPABASE_KEY, "
                        "SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.\n\n"
                        "**Fix:** Add your anon/public key to `.streamlit/secrets.toml`:\n"
                        "```toml\nSUPABASE_KEY = \"<your-anon-key>\"\n```"
                    )
                ),
            )

            # ------------------------------------------------------------------
            # 3. Edge Function URL construction
            # ------------------------------------------------------------------
            function_name = get_secret("SUPABASE_VEO_FUNCTION_NAME", "veo-generate").strip() or "veo-generate"
            invoke_url = ""
            fn_url_ok = False
            if url_ok:
                invoke_url = f"{supabase_url.rstrip('/')}/functions/v1/{function_name}"
                fn_url_ok = True
                _result_row(
                    results,
                    "Edge Function URL constructed",
                    True,
                    f"URL: `{invoke_url}`\n\nFunction name from secret "
                    f"`SUPABASE_VEO_FUNCTION_NAME`: **{function_name}**",
                )
            else:
                _result_row(
                    results,
                    "Edge Function URL constructed",
                    False,
                    "Skipped — SUPABASE_URL is not set.",
                )

            # ------------------------------------------------------------------
            # 4. CORS / reachability check (OPTIONS request — no credentials needed)
            # ------------------------------------------------------------------
            fn_reachable = False
            if fn_url_ok:
                try:
                    options_resp = requests.options(invoke_url, timeout=15)
                    # Supabase responds 200 or 204 to OPTIONS
                    fn_reachable = options_resp.status_code in {200, 204}
                    _result_row(
                        results,
                        "Edge Function is reachable (OPTIONS)",
                        fn_reachable,
                        (
                            f"HTTP {options_resp.status_code} — function endpoint responds to CORS preflight."
                            if fn_reachable
                            else (
                                f"HTTP {options_resp.status_code} — unexpected status.  "
                                "The function may not be deployed.\n\n"
                                "**Fix:** Deploy the edge function:\n"
                                "```bash\nsupabase functions deploy veo-generate --no-verify-jwt\n```"
                            )
                        ),
                    )
                except requests.exceptions.ConnectionError as exc:
                    _result_row(
                        results,
                        "Edge Function is reachable (OPTIONS)",
                        False,
                        f"Connection error: `{exc}`\n\n"
                        "Check that your SUPABASE_URL is correct and the Supabase project is active.",
                    )
                except requests.exceptions.Timeout:
                    _result_row(
                        results,
                        "Edge Function is reachable (OPTIONS)",
                        False,
                        "Request timed out after 15 s.  "
                        "The Supabase project may be paused or the URL may be wrong.",
                    )
                except Exception as exc:  # noqa: BLE001
                    _result_row(
                        results,
                        "Edge Function is reachable (OPTIONS)",
                        False,
                        f"Unexpected error: {exc}",
                    )
            else:
                _result_row(
                    results,
                    "Edge Function is reachable (OPTIONS)",
                    False,
                    "Skipped — Edge Function URL could not be constructed.",
                )

            # ------------------------------------------------------------------
            # 5. Invoke the Edge Function with a minimal test prompt
            #    This is the step most likely to surface the 500 error.
            #    We deliberately send a tiny prompt to trigger the Google API
            #    call and capture whatever the function throws back.
            # ------------------------------------------------------------------
            if fn_reachable and key_ok:
                try:
                    headers = {
                        "Authorization": f"Bearer {supabase_key}",
                        "apikey": supabase_key,
                        "Content-Type": "application/json",
                    }
                    test_payload = {"prompt": "A short diagnostic test clip.", "aspectRatio": "16:9"}
                    fn_resp = requests.post(
                        invoke_url,
                        json=test_payload,
                        headers=headers,
                        timeout=60,  # shorter than real call — we want the first failure, not a full Veo run
                    )
                    status = fn_resp.status_code

                    # Try to decode the JSON body regardless of status code
                    try:
                        body = fn_resp.json()
                    except Exception:  # noqa: BLE001
                        body = {"_raw": fn_resp.text[:1000]}

                    error_msg: str = str(body.get("error", "")) if isinstance(body, dict) else ""

                    if status == 200 and body.get("videoBase64"):
                        _result_row(
                            results,
                            "Edge Function invocation succeeded",
                            True,
                            "The function returned a `videoBase64` payload — Veo is fully working!",
                        )
                    elif status == 401:
                        _result_row(
                            results,
                            "Edge Function invocation succeeded",
                            False,
                            "HTTP 401 Unauthorized.\n\n"
                            "**Fix:** The function has JWT verification enabled. Redeploy without it:\n"
                            "```bash\nsupabase functions deploy veo-generate --no-verify-jwt\n```\n\n"
                            "Or confirm your SUPABASE_KEY is the correct anon/public key for this project.",
                        )
                    elif status == 500:
                        # Parse the internal error to give specific guidance
                        fix_hint = _veo_500_hint(error_msg)
                        _result_row(
                            results,
                            "Edge Function invocation succeeded",
                            False,
                            f"**HTTP 500 Internal Server Error** — the function crashed.\n\n"
                            f"**Error from function:** `{error_msg or '(no message in body)'}`\n\n"
                            f"{fix_hint}",
                        )
                    elif status == 404:
                        _result_row(
                            results,
                            "Edge Function invocation succeeded",
                            False,
                            "HTTP 404 — the function was not found at this URL.\n\n"
                            "**Fix:** Deploy it:\n"
                            "```bash\nsupabase functions deploy veo-generate --no-verify-jwt\n```\n\n"
                            f"Expected URL: `{invoke_url}`",
                        )
                    else:
                        _result_row(
                            results,
                            "Edge Function invocation succeeded",
                            False,
                            f"HTTP {status} — unexpected response.\n\n"
                            f"Body: `{str(body)[:600]}`",
                        )
                except requests.exceptions.Timeout:
                    # A timeout here is actually OK — it means the function IS
                    # running (auth + Vertex AI call started). Real generation
                    # takes minutes, so a 60 s timeout just means it got past
                    # the credential checks.
                    _result_row(
                        results,
                        "Edge Function invocation succeeded",
                        True,
                        "Request timed out at 60 s — this usually means the function **started "
                        "the Veo generation job successfully** (Veo can take 5-12 minutes). "
                        "Credentials appear to be correct. Use the full AI Video Generator tab "
                        "with a longer timeout to complete a real generation.",
                    )
                except Exception as exc:  # noqa: BLE001
                    _result_row(
                        results,
                        "Edge Function invocation succeeded",
                        False,
                        f"Request error: `{exc}`",
                    )
            else:
                _result_row(
                    results,
                    "Edge Function invocation succeeded",
                    False,
                    "Skipped — prerequisites (reachability or Supabase key) not met.",
                )

            _render_results(results, section="Veo")

            # Quick-reference fix guide
            st.divider()
            st.subheader("Veo Quick-Fix Reference")
            st.markdown(
                "| Symptom | Root Cause | Fix |\n"
                "|---------|-----------|-----|\n"
                "| `500` + *Missing Supabase secret GOOGLE_SERVICE_ACCOUNT_JSON* | Secret not set in Supabase | Add it in Supabase Dashboard → Edge Functions → Secrets |\n"
                "| `500` + *GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON* | Malformed JSON in secret | Re-paste the service account JSON, ensure no extra characters |\n"
                "| `500` + *Missing Supabase secret GOOGLE_CLOUD_PROJECT_ID* | Project ID not set | Add `GOOGLE_CLOUD_PROJECT_ID` to Supabase Edge Function secrets |\n"
                "| `500` + *Failed to obtain Google access token* | Wrong/expired service account key | Generate a new key in GCP Console → IAM → Service Accounts |\n"
                "| `500` + *Veo submit failed (403)* | Service account lacks Vertex AI permission | Grant `roles/aiplatform.user` to the service account in GCP |\n"
                "| `500` + *Veo submit failed (404)* | Veo model or location mismatch | Confirm `GOOGLE_CLOUD_LOCATION` is a region where Veo is available (e.g. `us-central1`) |\n"
                "| `401` | JWT verification enabled | Redeploy: `supabase functions deploy veo-generate --no-verify-jwt` |\n"
                "| `404` | Function not deployed | Deploy: `supabase functions deploy veo-generate --no-verify-jwt` |\n"
            )
            with st.expander("How to add Supabase Edge Function secrets"):
                st.markdown(
                    "1. Go to your **Supabase Dashboard** → **Edge Functions** → **veo-generate**  \n"
                    "2. Click **Secrets** (or use the Supabase CLI)  \n"
                    "3. Add the following secrets:\n\n"
                    "| Secret name | Value |\n"
                    "|-------------|-------|\n"
                    "| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON content of your GCP service account key file |\n"
                    "| `GOOGLE_CLOUD_PROJECT_ID` | Your GCP project ID (e.g. `my-project-123456`) |\n"
                    "| `GOOGLE_CLOUD_LOCATION` | Region where Veo is enabled (e.g. `us-central1`) |\n\n"
                    "Or via the CLI:\n"
                    "```bash\n"
                    "supabase secrets set GOOGLE_SERVICE_ACCOUNT_JSON='$(cat service-account.json)'\n"
                    "supabase secrets set GOOGLE_CLOUD_PROJECT_ID=my-project-123456\n"
                    "supabase secrets set GOOGLE_CLOUD_LOCATION=us-central1\n"
                    "```"
                )

        except Exception:
            st.error("Unexpected error during Veo diagnostics:")
            st.code(traceback.format_exc())
else:
    st.info("Click **Run Veo Diagnostics** to begin.")


def _veo_500_hint(error_msg: str) -> str:
    """Return a targeted fix hint based on the 500 error message from the edge function."""
    msg = error_msg.lower()

    if "google_service_account_json" in msg or "missing supabase secret" in msg and "service_account" in msg:
        return (
            "**Root cause:** The `GOOGLE_SERVICE_ACCOUNT_JSON` secret is not set in your Supabase "
            "Edge Function environment.\n\n"
            "**Fix:** In the Supabase Dashboard → Edge Functions → veo-generate → Secrets, add:\n"
            "- `GOOGLE_SERVICE_ACCOUNT_JSON` = *(paste the full JSON content of your GCP service account key)*"
        )

    if "not valid json" in msg or "invalid json" in msg or "json" in msg and "parse" in msg:
        return (
            "**Root cause:** The `GOOGLE_SERVICE_ACCOUNT_JSON` secret is not valid JSON.  "
            "This often happens when the JSON is pasted with line-break issues or truncated.\n\n"
            "**Fix:** Re-paste the complete service account JSON.  Make sure to copy the *entire* "
            "file content, including the opening `{` and closing `}`.  You can verify it with:\n"
            "```bash\npython3 -c \"import json, sys; json.load(open('service-account.json')); print('Valid JSON')\"\n```"
        )

    if "client_email" in msg or "private_key" in msg:
        return (
            "**Root cause:** The `GOOGLE_SERVICE_ACCOUNT_JSON` secret is missing required fields "
            "(`client_email` or `private_key`).\n\n"
            "**Fix:** Regenerate your service account key in GCP Console → IAM & Admin → Service Accounts, "
            "download as JSON, and re-paste into the Supabase secret."
        )

    if "google_cloud_project_id" in msg:
        return (
            "**Root cause:** The `GOOGLE_CLOUD_PROJECT_ID` secret is not set.\n\n"
            "**Fix:** Add it in Supabase Edge Function secrets:\n"
            "- `GOOGLE_CLOUD_PROJECT_ID` = your GCP project ID (e.g. `my-project-123456`)\n\n"
            "Find your project ID in the GCP Console top-left dropdown."
        )

    if "access token" in msg or "oauth" in msg or "jwt-bearer" in msg:
        return (
            "**Root cause:** Failed to obtain a Google OAuth access token.  The service account "
            "key may be wrong, expired, or the key file belongs to a different project.\n\n"
            "**Fix:**\n"
            "1. Go to GCP Console → IAM & Admin → Service Accounts  \n"
            "2. Select (or create) the service account  \n"
            "3. Keys tab → Add Key → Create new key → JSON  \n"
            "4. Update the `GOOGLE_SERVICE_ACCOUNT_JSON` Supabase secret with the new file  \n"
        )

    if "403" in msg or "permission" in msg or "forbidden" in msg:
        return (
            "**Root cause:** The Google service account does not have permission to call the Vertex AI / Veo API.\n\n"
            "**Fix:** In GCP Console → IAM & Admin → IAM, grant the service account the role:\n"
            "- `Vertex AI User` (`roles/aiplatform.user`)\n\n"
            "Also ensure the **Vertex AI API** is enabled for your project: "
            "GCP Console → APIs & Services → Enable APIs → search *Vertex AI API*."
        )

    if "404" in msg and ("veo" in msg or "model" in msg or "location" in msg):
        return (
            "**Root cause:** The Veo model endpoint returned 404.  The model name or location may be wrong, "
            "or Veo is not available in the selected region.\n\n"
            "**Fix:**\n"
            "1. Set `GOOGLE_CLOUD_LOCATION` to a supported region (e.g. `us-central1`)  \n"
            "2. Confirm that `veo-2.0-generate-001` is available in that region for your project  \n"
            "3. Check GCP Console → Vertex AI → Model Garden for Veo availability  \n"
        )

    if "timeout" in msg or "timed out" in msg:
        return (
            "**Root cause:** The Veo generation timed out inside the Edge Function (max ~12 minutes).\n\n"
            "This can mean:\n"
            "- The Veo API is under heavy load  \n"
            "- The prompt or parameters are unusually complex  \n\n"
            "**Fix:** Try again, or try a shorter/simpler prompt."
        )

    # Generic fallback
    return (
        "**Possible causes:**\n"
        "- `GOOGLE_SERVICE_ACCOUNT_JSON` not set or invalid in Supabase secrets  \n"
        "- `GOOGLE_CLOUD_PROJECT_ID` not set  \n"
        "- Service account lacks Vertex AI permissions  \n"
        "- Veo API not enabled for the GCP project  \n\n"
        "Check the Supabase Edge Function logs in the Dashboard for the full traceback."
    )


# ===========================================================================
# SECTION 2 — OpenAI Sora
# ===========================================================================

st.divider()
st.header("🟢 OpenAI Sora")
st.markdown(
    "Sora video generation is called **directly** from this app using your "
    "OpenAI API key.  Sora access requires a special API tier — this diagnostic "
    "checks every step from key configuration to model visibility."
)

if st.button("Run Sora Diagnostics", type="primary", key="sora_run"):
    with st.spinner("Running Sora checks…"):
        try:
            sora_results: list[tuple[str, bool, str]] = []

            # ------------------------------------------------------------------
            # 1. openai_api_key is set
            # ------------------------------------------------------------------
            raw_key = ""
            key_in_secrets = False
            try:
                if hasattr(st, "secrets") and "openai_api_key" in st.secrets:
                    raw_key = str(st.secrets["openai_api_key"]).strip()
                    key_in_secrets = True
            except Exception:  # noqa: BLE001
                pass

            resolved_key = get_secret("openai_api_key", "").strip()
            env_key = (
                os.getenv("openai_api_key", "").strip()
                or os.getenv("OPENAI_API_KEY", "").strip()
            )
            final_key = resolved_key or env_key

            _result_row(
                sora_results,
                "OpenAI API key is configured",
                bool(final_key),
                (
                    f"Key resolved — `{final_key[:7]}…` ({len(final_key)} chars)"
                    if final_key
                    else (
                        "No key found.  Checked: `st.secrets['openai_api_key']`, "
                        "`get_secret('openai_api_key')`, env vars `openai_api_key` / `OPENAI_API_KEY`.\n\n"
                        "**Fix:** Add to `.streamlit/secrets.toml`:\n"
                        "```toml\nopenai_api_key = \"sk-...\"\n```"
                    )
                ),
            )

            # ------------------------------------------------------------------
            # 2. Placeholder check
            # ------------------------------------------------------------------
            if final_key:
                placeholder_strings = {
                    "paste_key_here", "your_api_key_here", "replace_me",
                    "none", "null", "", "sk-...", "your-api-key",
                }
                is_placeholder = (
                    final_key.lower() in placeholder_strings
                    or final_key.lower().startswith("paste")
                    or final_key.lower().startswith("your_")
                )
                _result_row(
                    sora_results,
                    "Key is not a placeholder",
                    not is_placeholder,
                    (
                        "Value looks like a real key."
                        if not is_placeholder
                        else (
                            f"Value `{final_key[:30]}` looks like a placeholder.\n\n"
                            "**Fix:** Replace it with your real OpenAI API key from "
                            "**platform.openai.com/api-keys**."
                        )
                    ),
                )

            # ------------------------------------------------------------------
            # 3. Key format
            # ------------------------------------------------------------------
            if final_key:
                format_ok = final_key.startswith("sk-")
                _result_row(
                    sora_results,
                    "Key format valid (starts with `sk-`)",
                    format_ok,
                    (
                        f"Key: `{final_key[:10]}…{final_key[-4:]}`"
                        if format_ok
                        else (
                            f"Key starts with `{final_key[:10]}` — expected `sk-`.\n\n"
                            "**Fix:** Make sure you copied the full key from "
                            "**platform.openai.com/api-keys**.  Project API keys also "
                            "start with `sk-proj-…`."
                        )
                    ),
                )

            # ------------------------------------------------------------------
            # 4. Live OpenAI API call — GET /v1/models
            # ------------------------------------------------------------------
            if final_key:
                try:
                    resp = requests.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {final_key}"},
                        timeout=20,
                    )
                    if resp.status_code == 200:
                        model_ids: list[str] = [
                            m.get("id", "")
                            for m in resp.json().get("data", [])
                            if isinstance(m, dict)
                        ]
                        _result_row(
                            sora_results,
                            "Live OpenAI API call succeeded (GET /v1/models)",
                            True,
                            f"HTTP 200.  {len(model_ids)} models returned.",
                        )
                    elif resp.status_code == 401:
                        model_ids = []
                        _result_row(
                            sora_results,
                            "Live OpenAI API call succeeded (GET /v1/models)",
                            False,
                            "HTTP 401 Unauthorized — the key is invalid or has been revoked.\n\n"
                            "**Fix:** Generate a new API key at **platform.openai.com/api-keys**.",
                        )
                    else:
                        model_ids = []
                        _result_row(
                            sora_results,
                            "Live OpenAI API call succeeded (GET /v1/models)",
                            False,
                            f"HTTP {resp.status_code}: `{resp.text[:400]}`",
                        )
                except requests.exceptions.Timeout:
                    model_ids = []
                    _result_row(
                        sora_results,
                        "Live OpenAI API call succeeded (GET /v1/models)",
                        False,
                        "Request timed out.  Check your internet connection.",
                    )
                except Exception as exc:  # noqa: BLE001
                    model_ids = []
                    _result_row(
                        sora_results,
                        "Live OpenAI API call succeeded (GET /v1/models)",
                        False,
                        f"Request error: `{exc}`",
                    )
            else:
                model_ids = []
                _result_row(
                    sora_results,
                    "Live OpenAI API call succeeded (GET /v1/models)",
                    False,
                    "Skipped — no API key resolved.",
                )

            # ------------------------------------------------------------------
            # 5. Sora model visibility
            # ------------------------------------------------------------------
            if model_ids:
                sora_visible = "sora-2" in model_ids or "sora-2-pro" in model_ids
                found = [m for m in model_ids if "sora" in m.lower()]
                _result_row(
                    sora_results,
                    "Sora model visible for this API key (`sora-2` or `sora-2-pro`)",
                    sora_visible,
                    (
                        f"Sora models found: `{found}`"
                        if sora_visible
                        else (
                            f"No Sora models in the model list.  "
                            f"All visible models matching 'sora': `{found or 'none'}`\n\n"
                            "**This is the most common Sora issue.**\n\n"
                            "**Cause:** The API key belongs to an OpenAI org/project that does "
                            "**not** have Sora access.  Sora is a paid API-tier feature that "
                            "requires separate enablement.\n\n"
                            "**Fix:**\n"
                            "1. Log in to **platform.openai.com**  \n"
                            "2. Switch to the org/project that has Sora access  \n"
                            "3. Generate a new API key from that project  \n"
                            "4. Update `openai_api_key` in `.streamlit/secrets.toml`  \n\n"
                            "If you don't have Sora access yet, visit "
                            "**platform.openai.com/docs/guides/video** to check eligibility."
                        )
                    ),
                )
            elif final_key:
                _result_row(
                    sora_results,
                    "Sora model visible for this API key (`sora-2` or `sora-2-pro`)",
                    False,
                    "Skipped — model list could not be retrieved.",
                )

            # ------------------------------------------------------------------
            # 6. Endpoint accessibility — POST /v1/videos (expect 400/422, not 401/403)
            # ------------------------------------------------------------------
            if final_key:
                try:
                    probe_resp = requests.post(
                        "https://api.openai.com/v1/videos",
                        json={},  # intentionally empty to get a validation error, not auth error
                        headers={
                            "Authorization": f"Bearer {final_key}",
                            "Content-Type": "application/json",
                        },
                        timeout=20,
                    )
                    probe_status = probe_resp.status_code
                    try:
                        probe_body = probe_resp.json()
                    except Exception:  # noqa: BLE001
                        probe_body = {"_raw": probe_resp.text[:500]}

                    if probe_status in {400, 422}:
                        # Validation error = endpoint works and the key has access
                        _result_row(
                            sora_results,
                            "Sora endpoint accessible (POST /v1/videos)",
                            True,
                            f"HTTP {probe_status} (validation error for empty payload) — "
                            "the endpoint is accessible and your key has permission to call it.",
                        )
                    elif probe_status == 401:
                        _result_row(
                            sora_results,
                            "Sora endpoint accessible (POST /v1/videos)",
                            False,
                            "HTTP 401 Unauthorized — the key is invalid.\n\n"
                            "**Fix:** Generate a new key at **platform.openai.com/api-keys**.",
                        )
                    elif probe_status == 403:
                        _result_row(
                            sora_results,
                            "Sora endpoint accessible (POST /v1/videos)",
                            False,
                            "HTTP 403 Forbidden — your key does not have permission to use Sora.\n\n"
                            "**Fix:** Use an API key from an org/project that has Sora access enabled.",
                        )
                    elif probe_status == 404:
                        _result_row(
                            sora_results,
                            "Sora endpoint accessible (POST /v1/videos)",
                            False,
                            "HTTP 404 — the `/v1/videos` endpoint was not found.  "
                            "This may indicate the endpoint has changed or Sora is not available "
                            "for your account tier.\n\n"
                            f"Response: `{str(probe_body)[:400]}`",
                        )
                    else:
                        _result_row(
                            sora_results,
                            "Sora endpoint accessible (POST /v1/videos)",
                            False,
                            f"HTTP {probe_status}: `{str(probe_body)[:400]}`",
                        )
                except requests.exceptions.Timeout:
                    _result_row(
                        sora_results,
                        "Sora endpoint accessible (POST /v1/videos)",
                        False,
                        "Request timed out.",
                    )
                except Exception as exc:  # noqa: BLE001
                    _result_row(
                        sora_results,
                        "Sora endpoint accessible (POST /v1/videos)",
                        False,
                        f"Request error: `{exc}`",
                    )
            else:
                _result_row(
                    sora_results,
                    "Sora endpoint accessible (POST /v1/videos)",
                    False,
                    "Skipped — no API key resolved.",
                )

            _render_results(sora_results, section="Sora")

            st.divider()
            st.subheader("Sora Quick-Fix Reference")
            st.markdown(
                "| Symptom | Root Cause | Fix |\n"
                "|---------|-----------|-----|\n"
                "| `sora-2` not in model list | Key from wrong org/project | Switch to Sora-enabled project in platform.openai.com and generate a new key |\n"
                "| HTTP 401 | Invalid or revoked key | Generate a new key at platform.openai.com/api-keys |\n"
                "| HTTP 403 on /v1/videos | Key lacks Sora permission | Use the key from the project with Sora access |\n"
                "| HTTP 404 on /v1/videos | Endpoint not found / wrong tier | Confirm Sora API access with OpenAI support |\n"
                "| HTTP 400 on job create | Bad payload | Check model (`sora-2`/`sora-2-pro`), seconds (4/8/12), size format |\n"
                "| Job stuck in `pending` | Heavy load or long prompt | Wait longer or retry with a simpler prompt |\n"
            )

        except Exception:
            st.error("Unexpected error during Sora diagnostics:")
            st.code(traceback.format_exc())
else:
    st.info("Click **Run Sora Diagnostics** to begin.")


# ===========================================================================
# SECTION 3 — Shared: generated-videos Supabase bucket
# ===========================================================================

st.divider()
st.header("🗄️ Supabase Storage — `generated-videos` Bucket")
st.markdown(
    "Both Veo and Sora upload their results to the `generated-videos` Supabase "
    "storage bucket.  If the bucket is missing the video bytes are returned as a "
    "data URL instead, which may cause issues with large files."
)

if st.button("Check generated-videos Bucket", key="bucket_run"):
    with st.spinner("Checking bucket…"):
        try:
            supabase_url = get_secret("SUPABASE_URL", "").strip()
            supabase_key = (
                get_secret("SUPABASE_KEY", "").strip()
                or get_secret("SUPABASE_ANON_KEY", "").strip()
                or get_secret("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            )

            if not supabase_url or supabase_url in _PLACEHOLDER_URLS:
                st.error("SUPABASE_URL is not configured — cannot check storage.")
            elif not supabase_key or supabase_key in _PLACEHOLDER_KEYS:
                st.error("Supabase key is not configured — cannot check storage.")
            else:
                from supabase import create_client

                sb = create_client(supabase_url, supabase_key)
                try:
                    sb.storage.from_("generated-videos").list()
                    st.success(
                        "Bucket `generated-videos` exists and is accessible.  "
                        "Video uploads will work correctly."
                    )
                except Exception as exc:
                    st.error(f"Bucket `generated-videos` is **not accessible**: `{exc}`")
                    st.warning(
                        "**Fix:** Create the bucket in your Supabase Dashboard:\n\n"
                        "1. Go to **Storage** in the left sidebar  \n"
                        "2. Click **New Bucket**  \n"
                        "3. Name it `generated-videos`  \n"
                        "4. Set it to **Public** if you want direct URL access, or **Private** for signed URLs  \n"
                        "5. Click **Create Bucket**  \n\n"
                        "Or with the CLI:\n"
                        "```bash\nsupabase storage create-bucket generated-videos --public\n```"
                    )
        except Exception:
            st.error("Unexpected error:")
            st.code(traceback.format_exc())
else:
    st.info("Click **Check generated-videos Bucket** to verify storage.")
