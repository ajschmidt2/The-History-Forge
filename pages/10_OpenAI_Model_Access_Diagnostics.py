"""OpenAI model access diagnostics.

Use this page to validate whether the currently configured `openai_model`
is accessible for your API key/project, and to suggest a safe fallback.
"""

import streamlit as st

from src.config import get_secret
from src.lib.openai_config import DEFAULT_OPENAI_MODEL

PREFERRED_FALLBACK_MODELS = [
    DEFAULT_OPENAI_MODEL,
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
]

st.set_page_config(page_title="OpenAI Model Access Diagnostics", page_icon="🧪")
st.title("🧪 OpenAI Model Access Diagnostics")
st.caption(
    "Diagnoses model-access errors like: project does not have access to model `gpt-5-mini`."
)


def _mask_key(value: str) -> str:
    value = (value or "").strip()
    if len(value) < 10:
        return "(missing)"
    return f"{value[:7]}…{value[-4:]}"


def _pick_fallback_model(configured_model: str, model_ids: list[str]) -> str:
    """Pick the best fallback model from the account's accessible model list."""
    if configured_model in model_ids:
        return configured_model

    for model_id in PREFERRED_FALLBACK_MODELS:
        if model_id in model_ids:
            return model_id

    gpt_models = [model_id for model_id in model_ids if model_id.startswith("gpt-")]
    return gpt_models[0] if gpt_models else DEFAULT_OPENAI_MODEL


def run_diagnostics() -> None:
    results: list[tuple[str, bool, str]] = []

    api_key = get_secret("openai_api_key", "").strip()
    configured_model = get_secret("openai_model", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL

    if not api_key:
        results.append(("OpenAI API key resolved", False, "No `openai_api_key` was resolved from secrets/env."))
    else:
        results.append(("OpenAI API key resolved", True, f"Resolved key: `{_mask_key(api_key)}`"))

    results.append(("Configured model resolved", True, f"Configured `openai_model`: `{configured_model}`"))

    model_ids: list[str] = []
    suggested_model = DEFAULT_OPENAI_MODEL
    if api_key:
        try:
            import requests

            headers = {"Authorization": f"Bearer {api_key}"}
            model_resp = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=20)
            if model_resp.status_code == 200:
                model_ids = [m.get("id") for m in model_resp.json().get("data", []) if isinstance(m.get("id"), str)]
                suggested_model = _pick_fallback_model(configured_model, model_ids)
                results.append(
                    (
                        "List models endpoint works",
                        True,
                        f"HTTP 200. Found {len(model_ids)} models. Sample: {model_ids[:8]}",
                    )
                )
            else:
                results.append(
                    (
                        "List models endpoint works",
                        False,
                        f"HTTP {model_resp.status_code}: {model_resp.text[:300]}",
                    )
                )

            if model_ids:
                has_configured_model = configured_model in model_ids
                results.append(
                    (
                        "Configured model appears in model list",
                        has_configured_model,
                        (
                            f"`{configured_model}` is listed for this key/project."
                            if has_configured_model
                            else (
                                f"`{configured_model}` was not returned by `/v1/models`. "
                                f"Try `openai_model = \"{suggested_model}\"` in `.streamlit/secrets.toml`."
                            )
                        ),
                    )
                )

            tiny_payload = {
                "model": configured_model,
                "messages": [{"role": "user", "content": "Reply with: ok"}],
                "max_tokens": 5,
                "temperature": 0,
            }
            check_resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=tiny_payload,
                timeout=30,
            )
            if check_resp.status_code == 200:
                results.append(
                    (
                        "Configured model accepts chat completion",
                        True,
                        "HTTP 200 on tiny chat completion.",
                    )
                )
            else:
                detail = check_resp.text[:500]
                results.append(
                    (
                        "Configured model accepts chat completion",
                        False,
                        f"HTTP {check_resp.status_code}: {detail}",
                    )
                )

                if suggested_model != configured_model:
                    fallback_payload = {**tiny_payload, "model": suggested_model}
                    fallback_resp = requests.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=fallback_payload,
                        timeout=30,
                    )
                    results.append(
                        (
                            f"Fallback model `{suggested_model}` accepts chat completion",
                            fallback_resp.status_code == 200,
                            (
                                "HTTP 200. This model is a good immediate fallback."
                                if fallback_resp.status_code == 200
                                else f"HTTP {fallback_resp.status_code}: {fallback_resp.text[:300]}"
                            ),
                        )
                    )

        except Exception as exc:  # noqa: BLE001
            results.append(("OpenAI connectivity checks", False, f"Request exception: {exc}"))

    all_passed = all(ok for _, ok, _ in results)
    if all_passed:
        st.success("All model-access checks passed.")
    else:
        st.error("One or more checks failed. Review the failing sections below.")

    st.divider()
    for label, passed, detail in results:
        icon = "✅" if passed else "❌"
        with st.expander(f"{icon} {label}", expanded=not passed):
            st.write(detail)

    if not all_passed:
        st.divider()
        st.subheader("Suggested fix")
        st.code(
            f'[default]\nopenai_api_key = "sk-..."\nopenai_model = "{suggested_model}"\n',
            language="toml",
        )
        st.markdown(
            "1. Open `.streamlit/secrets.toml`.\n"
            f"2. Set `openai_model` to `{suggested_model}` (or another model your project can access).\n"
            "3. Restart Streamlit and re-run this diagnostic page."
        )


if st.button("Run Model Access Diagnostics", type="primary"):
    run_diagnostics()
