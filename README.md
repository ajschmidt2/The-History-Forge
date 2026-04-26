# History Video Generator

Generate engaging YouTubeâ€style history videos with AI. This project is a
**frictionless, artifact-first** Streamlit web app that produces:

* a polished narration script (ready for voiceover)
* a scene-by-scene visual plan
* **real generated images** aligned to each scene (optional toggle)
* an exportable ZIP bundle for your editor

Under the hood it uses OpenAI for scripts/voiceover and Google's current
**GenAI SDK** with a Gemini Developer API key for Gemini image/video media.

## Features

* **Topic & tone selection** â€“ describe the historical event or subject you want
  to cover, pick the length (short/standard/long) and choose a tone such as
  *cinematic*, *mysterious*, *educational* or *eerie*.
* **Script generation** â€“ uses OpenAIâ€™s ChatGPT API to generate a complete
  narrative script tailored to your chosen settings.  The script is free of
  visual cues so you can easily paste it into a teleprompter or editing
  software.
* **Gemini-inspired UI** â€“ script and visuals are treated as editable artifacts
  (not chat logs), with progressive generation steps and per-scene controls.
* **Scene planning & prompts** â€“ uses structured JSON planning so visuals stay
  coherent and aligned with the narration.
* **Optional image generation** â€“ generates real images using Gemini image-capable
  models (e.g. `gemini-2.5-flash-image`). When image generation fails, the app
  falls back to placeholders so you can still export a usable package.
* **Shorts-first AI video clips** â€“ generates a small number of Gemini/Veo clips
  through the Gemini Developer API, with fal.ai kept as an optional fallback.
* **Per-scene regeneration & refinement** â€“ refine the whole script or just a
  single scene prompt, then regenerate only that sceneâ€™s image.
* **Export ZIP** â€“ script + `scenes.json` + images (PNG) in a tidy folder.
* **Extensible architecture** â€“ utility functions live in `utils.py` and are
  designed to be swapped out or extended.  Add new tones, alternative
  providers, or export formats without touching the main UI.

## Project layout

```text
youtube_history_generator/
â”œâ”€â”€ app.py                # Main Streamlit application
â”œâ”€â”€ utils.py              # Provider logic (OpenAI text + AI Studio images)
â”œâ”€â”€ image_gen.py          # AI Studio Imagen helper
â”œâ”€â”€ requirements.txt      # Python dependencies
â”œâ”€â”€ README.md             # This file
â”œâ”€â”€ .gitignore            # Ignore common Python artefacts
â””â”€â”€ .streamlit/
    â””â”€â”€ secrets.toml      # API keys (never commit real secrets)
```

## Generate visuals from your own script (copy/paste helper)

If you already have a finished narration script and just want visuals, you can
copy/paste this helper from `utils.py` into your own script or project. It
splits the script into scenes, writes image prompts, generates images, and
returns the list of `Scene` objects plus a failure count.

```python
from utils import generate_visuals_from_script

scenes, failures = generate_visuals_from_script(
    script=my_script_text,
    num_images=8,
    tone="Cinematic",
    visual_style="Photorealistic cinematic",
    aspect_ratio="16:9",
    variations_per_scene=1,
)

# Each Scene includes .image_prompt, .image_bytes, and .image_variations.
```

## Setup

1. **Clone the repository** or copy these files into your own project.
2. Install dependencies (preferably inside a virtual environment):

```bash
pip install -r requirements.txt
```

3. Create a `.streamlit/secrets.toml` file in the project root (one is
   included as a template). Place your API keys there:

```toml
openai_api_key = "sk-..."
GEMINI_API_KEY = "AIza..."

# Optional overrides
openai_model = "gpt-4o-mini"
GEMINI_MODEL_TEXT = "gemini-2.5-flash"
GEMINI_MODEL_FAST = "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_VIDEO_MODEL = "veo-3.1-lite-generate-preview"

# Optional free B-roll providers
PEXELS_API_KEY = "your_pexels_key"
PIXABAY_API_KEY = "your_pixabay_key"
```

### Free B-roll API setup notes (Pexels + Pixabay)

- Expected secret names are exactly:
  - `PEXELS_API_KEY`
  - `PIXABAY_API_KEY`
- Restart Streamlit after editing `.streamlit/secrets.toml`, otherwise new secret values will not be visible to the running app.
- Pexels video search uses `https://api.pexels.com/videos/search` with the HTTP header:
  - `Authorization: <PEXELS_API_KEY>`
- Pixabay video search uses `https://pixabay.com/api/videos/` with the query parameter:
  - `key=<PIXABAY_API_KEY>`
- In the **ðŸŽžï¸ B-Roll** tab, use the diagnostics panel to confirm provider configuration before searching.

4. Run the app:

```bash
streamlit run app.py
```

## GitHub Actions secrets for unattended daily runs

If you want the scheduled daily workflow in `.github/workflows/daily-video.yml` to run and post without your local app open, add these GitHub Actions repository secrets:

```text
OPENAI_API_KEY
GEMINI_API_KEY
SUPABASE_URL
SUPABASE_KEY
SUPABASE_SERVICE_ROLE_KEY
META_APP_ID
META_APP_SECRET
INSTAGRAM_USER_ID
INSTAGRAM_ACCESS_TOKEN
YOUTUBE_CLIENT_SECRETS_JSON
YOUTUBE_TOKEN_JSON
```

Notes:

- `YOUTUBE_CLIENT_SECRETS_JSON` should contain the full JSON contents of your local `client_secrets.json`.
- `YOUTUBE_TOKEN_JSON` should contain the full JSON contents of your local `token.json`.
- The workflow writes those two secrets into temporary files on the GitHub runner before executing `python -m src.workflow.daily_job`.
- Local `.streamlit/secrets.toml` values are not automatically available to GitHub Actions; they must be mirrored into repository secrets for unattended scheduled posting.

Streamlit will start a local web server (usually at http://localhost:8501) where you can
enter a topic, pick settings and generate a script and images.  API keys are
loaded securely via Streamlitâ€™s secrets manager and never exposed in your
source code.

## Gemini Developer API setup

Create a Gemini API key in Google AI Studio, then store it as `GEMINI_API_KEY`
in `.streamlit/secrets.toml`, Streamlit Cloud secrets, Vercel environment
variables, or your local shell environment.

The app no longer uses Google Cloud project-based generative media. You do not need
`GOOGLE_APPLICATION_CREDENTIALS`, Google Cloud project/location settings, or a
service-account JSON for model calls.

## Python SDK quickstart (Google GenAI SDK)

```bash
pip install google-genai
```

```python
from google import genai
import os

def generate():
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
        http_options={"api_version": "v1beta"},
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Write a 20-word hook for a history short.",
    )
    print(response.text)

if __name__ == "__main__":
    generate()
```

## Future directions

This project is intentionally simple to serve as a foundation.  Here are a few
ideas for enhancements you might consider:

* **Batch generation:** allow the user to enter multiple topics and generate
  several scripts at once, optionally exporting them as a CSV or JSON file.
* **Shortâ€‘form extraction:** automatically cut a longer script into a series of
  shorts (e.g. 60 seconds each) with their own hooks and visuals.
* **Additional tones and audiences:** add more tonal options or adjust
  complexity for different age groups (e.g. kids vs. adults).
* **Alternative providers:** integrate other image models (e.g. DALLÂ·E or
  Midjourney) or text models (e.g. Anthropicâ€™s Claude) by implementing
  alternative functions in `utils.py`.
* **One-click video assembly:** stitch images + voiceover + captions into an
  MP4 (server-side) or export an edit-friendly timeline (e.g., Premiere XML).
* **Brand presets:** reusable channel presets (tone, CTA wording, scene count,
  visual style) with saved profiles.

Contributions are welcome!  Feel free to fork this project and adapt it to your
own creative workflow.

