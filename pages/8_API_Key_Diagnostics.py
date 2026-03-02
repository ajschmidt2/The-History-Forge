"""OpenAI API key diagnostic page.

Run this page to pinpoint exactly where the openai_api_key lookup is
failing and whether the key itself is valid.
"""
import os
import traceback

import streamlit as st

st.set_page_config(page_title="API Key Diagnostics", page_icon="🔑")
st.title("🔑 OpenAI API Key Diagnostics")
st.caption(
    "This page traces every step of the `openai_api_key` lookup so you can "
    "see exactly where things go wrong."
)


def run_diagnostics() -> None:
    results: list[tuple[str, bool, str]] = []  # (label, passed, detail)

    # ------------------------------------------------------------------
    # 1. Streamlit secrets presence
    # ------------------------------------------------------------------
    secrets_available = False
    secrets_keys: list[str] = []
    try:
        if hasattr(st, "secrets"):
            secrets_keys = list(st.secrets.keys())
            secrets_available = True
    except Exception as exc:
        results.append(("Streamlit secrets accessible", False, str(exc)))
    else:
        results.append(
            (
                "Streamlit secrets accessible",
                secrets_available,
                f"Keys present: {secrets_keys}" if secrets_keys else "Secrets object exists but is empty.",
            )
        )

    # ------------------------------------------------------------------
    # 2. openai_api_key in Streamlit secrets (exact key name)
    # ------------------------------------------------------------------
    key_in_secrets = False
    raw_secret_value = ""
    if secrets_available:
        if "openai_api_key" in st.secrets:
            raw_secret_value = str(st.secrets["openai_api_key"])
            key_in_secrets = True
            results.append(
                (
                    "`openai_api_key` found in st.secrets",
                    True,
                    f"Value length: {len(raw_secret_value)} chars, "
                    f"starts with: `{raw_secret_value[:7]}…`",
                )
            )
        else:
            # Check if OPENAI_API_KEY (uppercase) is there as a fallback hint
            uppercase_present = "OPENAI_API_KEY" in st.secrets
            results.append(
                (
                    "`openai_api_key` found in st.secrets",
                    False,
                    (
                        "Key not found. "
                        + (
                            "However `OPENAI_API_KEY` (uppercase) IS present — rename it to "
                            "`openai_api_key` in .streamlit/secrets.toml to fix."
                            if uppercase_present
                            else "Neither `openai_api_key` nor `OPENAI_API_KEY` found in secrets."
                        )
                    ),
                )
            )

    # ------------------------------------------------------------------
    # 3. Placeholder / empty check on raw secret
    # ------------------------------------------------------------------
    if key_in_secrets:
        placeholder_strings = {
            "paste_key_here", "your_api_key_here", "replace_me",
            "none", "null", "", "sk-...", "your-api-key",
        }
        is_placeholder = (
            raw_secret_value.strip().lower() in placeholder_strings
            or raw_secret_value.strip().lower().startswith("paste")
            or raw_secret_value.strip().lower().startswith("your_")
        )
        if is_placeholder:
            results.append(
                (
                    "Secret value is not a placeholder",
                    False,
                    f"Value `{raw_secret_value[:30]}` looks like a placeholder. "
                    "Replace it with your real OpenAI key.",
                )
            )
        else:
            results.append(
                (
                    "Secret value is not a placeholder",
                    True,
                    "Value looks like a real key.",
                )
            )

    # ------------------------------------------------------------------
    # 4. get_secret() from src.config resolves the key
    # ------------------------------------------------------------------
    resolved_key = ""
    try:
        from src.config import get_secret
        resolved_key = get_secret("openai_api_key", "").strip()
        if resolved_key:
            results.append(
                (
                    "`get_secret('openai_api_key')` returns a value",
                    True,
                    f"Resolved length: {len(resolved_key)} chars, "
                    f"starts with: `{resolved_key[:7]}…`",
                )
            )
        else:
            results.append(
                (
                    "`get_secret('openai_api_key')` returns a value",
                    False,
                    "Returned empty string — key is missing or normalised to empty (placeholder).",
                )
            )
    except Exception as exc:
        results.append(
            (
                "`get_secret('openai_api_key')` returns a value",
                False,
                f"Import/call error: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 5. Environment variable fallback
    # ------------------------------------------------------------------
    env_key = os.getenv("openai_api_key", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if env_key:
        results.append(
            (
                "Key found in environment variables",
                True,
                f"Found via env var — length {len(env_key)}, starts with `{env_key[:7]}…`.",
            )
        )
    else:
        results.append(
            (
                "Key found in environment variables",
                False,
                "Neither `openai_api_key` nor `OPENAI_API_KEY` set as environment variables "
                "(this is fine if Streamlit secrets are used instead).",
            )
        )

    # ------------------------------------------------------------------
    # 6. Key format check (should start with sk-)
    # ------------------------------------------------------------------
    final_key = resolved_key or env_key
    if final_key:
        if final_key.startswith("sk-"):
            results.append(
                (
                    "Key format valid (starts with `sk-`)",
                    True,
                    f"Key: `{final_key[:10]}…{final_key[-4:]}`",
                )
            )
        else:
            results.append(
                (
                    "Key format valid (starts with `sk-`)",
                    False,
                    f"Key starts with `{final_key[:10]}` — expected `sk-`. "
                    "Make sure you copied the full key from platform.openai.com.",
                )
            )

    # ------------------------------------------------------------------
    # 7. Live API connectivity test (GET /v1/models)
    # ------------------------------------------------------------------
    if final_key:
        try:
            import requests

            resp = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {final_key}"},
                timeout=15,
            )
            if resp.status_code == 200:
                model_ids = [m.get("id") for m in resp.json().get("data", [])]
                gpt_models = [m for m in model_ids if isinstance(m, str) and "gpt" in m]
                results.append(
                    (
                        "Live OpenAI API call succeeded (GET /v1/models)",
                        True,
                        f"HTTP 200. {len(model_ids)} models returned. "
                        f"Sample GPT models: {gpt_models[:5]}",
                    )
                )
            elif resp.status_code == 401:
                results.append(
                    (
                        "Live OpenAI API call succeeded (GET /v1/models)",
                        False,
                        "HTTP 401 Unauthorized — the key is invalid or has been revoked. "
                        "Generate a new key at platform.openai.com/api-keys.",
                    )
                )
            else:
                results.append(
                    (
                        "Live OpenAI API call succeeded (GET /v1/models)",
                        False,
                        f"HTTP {resp.status_code}: {resp.text[:300]}",
                    )
                )
        except Exception as exc:
            results.append(
                (
                    "Live OpenAI API call succeeded (GET /v1/models)",
                    False,
                    f"Request exception: {exc}",
                )
            )
    else:
        results.append(
            (
                "Live OpenAI API call succeeded (GET /v1/models)",
                False,
                "Skipped — no key resolved in previous steps.",
            )
        )

    # ------------------------------------------------------------------
    # Render results
    # ------------------------------------------------------------------
    all_passed = all(ok for _, ok, _ in results)

    if all_passed:
        st.success("All checks passed — `openai_api_key` is configured correctly.")
    else:
        first_fail = next((label for label, ok, _ in results if not ok), None)
        st.error(f"One or more checks failed. First failure: **{first_fail}**")

    st.divider()
    for label, passed, detail in results:
        icon = "✅" if passed else "❌"
        with st.expander(f"{icon} {label}", expanded=not passed):
            st.write(detail)

    # ------------------------------------------------------------------
    # Quick-fix instructions
    # ------------------------------------------------------------------
    if not all_passed:
        st.divider()
        st.subheader("How to fix")

        cloud_tab, local_tab = st.tabs(["Streamlit Community Cloud", "Local / self-hosted"])

        with cloud_tab:
            st.markdown(
                "The API key must be set in the **Streamlit Community Cloud dashboard** — "
                "the committed `.streamlit/secrets.toml` file is **not** read by Cloud "
                "deployments.\n\n"
                "**Steps:**\n"
                "1. Go to [share.streamlit.io](https://share.streamlit.io) and open your app.\n"
                "2. Click **⋮ → Settings → Secrets**.\n"
                "3. Add or update the following (replace the placeholder with your real key):\n"
            )
            st.code(
                'openai_api_key = "sk-..."   # ← paste your real OpenAI key here\n',
                language="toml",
            )
            st.markdown(
                "4. Click **Save** — Streamlit will restart the app automatically.\n"
                "5. Re-run this diagnostic page to confirm.\n\n"
                "> **Tip:** get your key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys)."
            )

        with local_tab:
            st.markdown(
                "Edit `.streamlit/secrets.toml` in your project root "
                "(create it if it does not exist):\n"
            )
            st.code(
                'openai_api_key = "sk-..."   # ← paste your real key here\n'
                'GEMINI_API_KEY = "AIza..."\n'
                'elevenlabs_api_key = ""\n',
                language="toml",
            )
            st.markdown(
                "1. Replace `sk-...` with your real key (from **platform.openai.com/api-keys**).\n"
                "2. Save the file and **restart** the Streamlit app (`Ctrl-C` then `streamlit run app.py`).\n"
                "3. Re-run this diagnostic page to confirm.\n\n"
                "> **Note:** `.streamlit/secrets.toml` is in `.gitignore` — never commit real keys."
            )


if st.button("Run Diagnostics", type="primary"):
    with st.spinner("Running checks…"):
        try:
            run_diagnostics()
        except Exception:
            st.error("Unexpected error during diagnostics:")
            st.code(traceback.format_exc())
else:
    st.info("Click **Run Diagnostics** to start the checks.")
