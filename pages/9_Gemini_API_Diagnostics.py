"""Gemini / Google AI Studio API diagnostics page.

Run this page to pinpoint exactly where the Gemini API key lookup is failing
and whether the key itself can authenticate against Google's generative-language
and Imagen endpoints.
"""
import os
import traceback

import streamlit as st

st.set_page_config(page_title="Gemini API Diagnostics", page_icon="🔮")
st.title("🔮 Gemini / Google AI API Diagnostics")
st.caption(
    "This page traces every step of the Gemini API key lookup so you can "
    "see exactly where things go wrong, then verifies live connectivity to "
    "Google's Generative Language API."
)

# All key names that the app accepts, in priority order.
_KEY_NAMES = (
    "GEMINI_API_KEY",
    "GOOGLE_AI_STUDIO_API_KEY",
    "GOOGLE_API_KEY",
    "gemini_api_key",
    "google_ai_studio_api_key",
    "google_api_key",
)

_PLACEHOLDER_VALUES = {
    "paste_key_here", "your_api_key_here", "replace_me",
    "none", "null", "", "aiza...", "your-api-key", "your_key_here",
}

_MODELS_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)

# Image-generation models the app tries to use.
_IMAGE_MODELS = (
    "gemini-2.5-flash-image",
    "imagen-3.0-generate-002",
    "imagen-3.0-generate-001",
)


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return (
        v in _PLACEHOLDER_VALUES
        or v.startswith("paste")
        or v.startswith("your_")
        or v.startswith("your-")
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
    # 2. Scan all accepted key names in Streamlit secrets
    # ------------------------------------------------------------------
    raw_secret_value = ""
    found_secret_name = ""
    if secrets_available:
        for key_name in _KEY_NAMES:
            if key_name in st.secrets:
                raw_secret_value = str(st.secrets[key_name])
                found_secret_name = key_name
                break

        if found_secret_name:
            results.append(
                (
                    "Gemini key found in st.secrets",
                    True,
                    f"Found under `{found_secret_name}`. "
                    f"Value length: {len(raw_secret_value)} chars, "
                    f"starts with: `{raw_secret_value[:8]}…`",
                )
            )
        else:
            results.append(
                (
                    "Gemini key found in st.secrets",
                    False,
                    f"None of the accepted key names were found in st.secrets. "
                    f"Accepted names: {', '.join(f'`{k}`' for k in _KEY_NAMES[:3])} (and lowercase variants).",
                )
            )

    # ------------------------------------------------------------------
    # 3. Placeholder / empty check on raw secret
    # ------------------------------------------------------------------
    if raw_secret_value:
        if _is_placeholder(raw_secret_value):
            results.append(
                (
                    "Secret value is not a placeholder",
                    False,
                    f"Value `{raw_secret_value[:30]}` looks like a placeholder. "
                    "Replace it with your real Google AI Studio API key.",
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
    # 4. _resolve_api_key() from image_gen resolves the key
    # ------------------------------------------------------------------
    resolved_key = ""
    try:
        from image_gen import _resolve_api_key  # type: ignore[import]

        resolved_key = _resolve_api_key()
        if resolved_key:
            results.append(
                (
                    "`image_gen._resolve_api_key()` returns a value",
                    True,
                    f"Resolved length: {len(resolved_key)} chars, "
                    f"starts with: `{resolved_key[:8]}…`",
                )
            )
        else:
            results.append(
                (
                    "`image_gen._resolve_api_key()` returns a value",
                    False,
                    "Returned empty string — key is missing or normalised to empty (placeholder).",
                )
            )
    except Exception as exc:
        results.append(
            (
                "`image_gen._resolve_api_key()` returns a value",
                False,
                f"Import/call error: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 5. Environment variable fallback
    # ------------------------------------------------------------------
    env_key = ""
    env_key_name = ""
    for key_name in _KEY_NAMES:
        val = os.getenv(key_name, "").strip()
        if val and not _is_placeholder(val):
            env_key = val
            env_key_name = key_name
            break

    if env_key:
        results.append(
            (
                "Key found in environment variables",
                True,
                f"Found via `{env_key_name}` — length {len(env_key)}, "
                f"starts with `{env_key[:8]}…`.",
            )
        )
    else:
        results.append(
            (
                "Key found in environment variables",
                False,
                "None of the accepted key names are set as environment variables "
                "(this is fine if Streamlit secrets are used instead).",
            )
        )

    # ------------------------------------------------------------------
    # 6. Key format check (Google AI Studio keys start with "AIza")
    # ------------------------------------------------------------------
    final_key = resolved_key or env_key
    if final_key:
        if final_key.startswith("AIza"):
            results.append(
                (
                    "Key format valid (starts with `AIza`)",
                    True,
                    f"Key: `{final_key[:10]}…{final_key[-4:]}`",
                )
            )
        else:
            results.append(
                (
                    "Key format valid (starts with `AIza`)",
                    False,
                    f"Key starts with `{final_key[:10]}` — Google AI Studio keys normally "
                    "begin with `AIza`. Make sure you copied the full key from "
                    "aistudio.google.com/app/apikey.",
                )
            )

    # ------------------------------------------------------------------
    # 7. Live API connectivity — list models
    # ------------------------------------------------------------------
    model_ids: list[str] = []
    if final_key:
        try:
            import requests

            resp = requests.get(
                _MODELS_API_URL,
                params={"key": final_key},
                timeout=15,
            )
            if resp.status_code == 200:
                model_ids = [
                    m.get("name", "")
                    for m in resp.json().get("models", [])
                ]
                results.append(
                    (
                        "Live Google Generative Language API call succeeded",
                        True,
                        f"HTTP 200. {len(model_ids)} models returned. "
                        f"Sample: {model_ids[:5]}",
                    )
                )
            elif resp.status_code == 400:
                results.append(
                    (
                        "Live Google Generative Language API call succeeded",
                        False,
                        "HTTP 400 Bad Request — the API key format is invalid. "
                        "Generate a new key at aistudio.google.com/app/apikey.",
                    )
                )
            elif resp.status_code == 403:
                results.append(
                    (
                        "Live Google Generative Language API call succeeded",
                        False,
                        "HTTP 403 Forbidden — the key is valid but lacks permission to "
                        "use the Generative Language API. Enable it in the Google Cloud Console.",
                    )
                )
            else:
                results.append(
                    (
                        "Live Google Generative Language API call succeeded",
                        False,
                        f"HTTP {resp.status_code}: {resp.text[:300]}",
                    )
                )
        except Exception as exc:
            results.append(
                (
                    "Live Google Generative Language API call succeeded",
                    False,
                    f"Request exception: {exc}",
                )
            )
    else:
        results.append(
            (
                "Live Google Generative Language API call succeeded",
                False,
                "Skipped — no key resolved in previous steps.",
            )
        )

    # ------------------------------------------------------------------
    # 8. Image-generation model availability
    # ------------------------------------------------------------------
    if model_ids:
        # Strip "models/" prefix for easier comparison.
        bare_ids = {m.removeprefix("models/") for m in model_ids}
        found_image_models: list[str] = []
        for model in _IMAGE_MODELS:
            if model in bare_ids or f"models/{model}" in model_ids:
                found_image_models.append(model)

        if found_image_models:
            results.append(
                (
                    "Image-generation model(s) available",
                    True,
                    f"Found: {', '.join(found_image_models)}",
                )
            )
        else:
            results.append(
                (
                    "Image-generation model(s) available",
                    False,
                    f"None of the expected image models were listed: "
                    f"{', '.join(_IMAGE_MODELS)}. "
                    "The models API list may be incomplete; a direct generation "
                    "call could still succeed.",
                )
            )
    elif final_key:
        results.append(
            (
                "Image-generation model(s) available",
                False,
                "Skipped — model list could not be retrieved (see previous check).",
            )
        )

    # ------------------------------------------------------------------
    # Render results
    # ------------------------------------------------------------------
    all_passed = all(ok for _, ok, _ in results)

    if all_passed:
        st.success("All checks passed — Gemini/Google API key is configured correctly.")
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
            '[default]\nopenai_api_key = "sk-..."\n'
            'GEMINI_API_KEY = "AIza..."   # ← paste your real Google AI Studio key here\n'
            'elevenlabs_api_key = ""\n',
            language="toml",
        )
        st.markdown(
            "1. Open `.streamlit/secrets.toml` in your project root.  \n"
            "2. Set `GEMINI_API_KEY` to a key from **[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)**.  \n"
            "3. Save the file and **restart** the Streamlit app.  \n"
            "4. Re-run this diagnostic page to confirm."
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
