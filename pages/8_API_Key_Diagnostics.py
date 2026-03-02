"""OpenAI API key diagnostic page.

Run this page to pinpoint exactly where the openai_api_key lookup is
failing and whether the key itself is valid.
"""
import traceback

import streamlit as st

from src.config import get_secret, resolve_openai_key, streamlit_secrets_detected

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
    secrets_available = streamlit_secrets_detected()
    results.append(
        (
            "Streamlit secrets accessible",
            secrets_available,
            "Detected via central config loader." if secrets_available else "No populated Streamlit secrets detected.",
        )
    )

    # Temporary safe debug visibility for secrets wiring (no full key output)
    st.write("Has OPENAI_API_KEY in st.secrets:", "OPENAI_API_KEY" in st.secrets)
    st.write("Has openai_api_key in st.secrets:", "openai_api_key" in st.secrets)

    val = st.secrets.get("OPENAI_API_KEY", "") or st.secrets.get("openai_api_key", "")
    val = str(val)
    st.write("Key length:", len(val.strip()))
    if val.strip():
        st.write("Key preview:", val.strip()[:6] + "..." + val.strip()[-4:])

    # ------------------------------------------------------------------
    # 2. openai_api_key in Streamlit secrets (exact key name)
    # ------------------------------------------------------------------
    raw_secret_value = resolve_openai_key()
    key_in_secrets = bool(raw_secret_value and secrets_available)
    results.append(
        (
            "OpenAI key resolved by loader",
            bool(raw_secret_value),
            (
                f"Value length: {len(raw_secret_value)} chars, starts with: `{raw_secret_value[:7]}…`"
                if raw_secret_value
                else "Loader did not find a configured OpenAI key."
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
        resolved_key = resolve_openai_key()
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
    env_key = get_secret("OPENAI_API_KEY", "").strip()
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
        st.code(
            '[default]\nOPENAI_API_KEY = "sk-..."\nopenai_api_key = "sk-..."\n'
            'GEMINI_API_KEY = "AIza..."\nelevenlabs_api_key = ""\n',
            language="toml",
        )
        st.markdown(
            "1. Open `.streamlit/secrets.toml` in your project root.  \n"
            "2. Set both `OPENAI_API_KEY` and `openai_api_key` to your real key (from **platform.openai.com/api-keys**).  \n"
            "3. Save the file and **restart** the Streamlit app.  \n"
            "4. If you also use a `[openai]` section, keep it, but ensure the top-level keys above exist.  \n"
            "5. Re-run this diagnostic page to confirm."
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
