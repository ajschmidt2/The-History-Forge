# History Video Generator

Generate engaging YouTube‐style history videos with AI. This project is a
**frictionless, artifact-first** Streamlit web app that produces:

* a polished narration script (ready for voiceover)
* a scene-by-scene visual plan
* **real generated images** aligned to each scene (optional toggle)
* an exportable ZIP bundle for your editor

Under the hood it uses OpenAI for text and Google’s **Gen AI SDK** (AI Studio
Imagen models) for images.

## Features

* **Topic & tone selection** – describe the historical event or subject you want
  to cover, pick the length (short/standard/long) and choose a tone such as
  *cinematic*, *mysterious*, *educational* or *eerie*.
* **Script generation** – uses OpenAI’s ChatGPT API to generate a complete
  narrative script tailored to your chosen settings.  The script is free of
  visual cues so you can easily paste it into a teleprompter or editing
  software.
* **Gemini-inspired UI** – script and visuals are treated as editable artifacts
  (not chat logs), with progressive generation steps and per-scene controls.
* **Scene planning & prompts** – uses structured JSON planning so visuals stay
  coherent and aligned with the narration.
* **Optional image generation** – generates real images using AI Studio Imagen
  models (e.g. `imagen-3.0-generate-002`). When image generation fails, the app
  falls back to placeholders so you can still export a usable package.
* **Per-scene regeneration & refinement** – refine the whole script or just a
  single scene prompt, then regenerate only that scene’s image.
* **Export ZIP** – script + `scenes.json` + images (PNG) in a tidy folder.
* **Extensible architecture** – utility functions live in `utils.py` and are
  designed to be swapped out or extended.  Add new tones, alternative
  providers, or export formats without touching the main UI.

## Project layout

```text
youtube_history_generator/
├── app.py                # Main Streamlit application
├── utils.py              # Provider logic (OpenAI text + AI Studio images)
├── image_gen.py          # AI Studio Imagen helper
├── requirements.txt      # Python dependencies
├── README.md             # This file
├── .gitignore            # Ignore common Python artefacts
└── .streamlit/
    └── secrets.toml      # API keys (never commit real secrets)
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
GOOGLE_AI_STUDIO_API_KEY = "AIza..."

# Optional overrides
openai_model = "gpt-4.1-mini"
GOOGLE_AI_STUDIO_IMAGE_MODEL = "imagen-3.0-generate-002"
```

4. Run the app:

```bash
streamlit run app.py
```

Streamlit will start a local web server (usually at http://localhost:8501) where you can
enter a topic, pick settings and generate a script and images.  API keys are
loaded securely via Streamlit’s secrets manager and never exposed in your
source code.

## Future directions

This project is intentionally simple to serve as a foundation.  Here are a few
ideas for enhancements you might consider:

* **Batch generation:** allow the user to enter multiple topics and generate
  several scripts at once, optionally exporting them as a CSV or JSON file.
* **Short‑form extraction:** automatically cut a longer script into a series of
  shorts (e.g. 60 seconds each) with their own hooks and visuals.
* **Additional tones and audiences:** add more tonal options or adjust
  complexity for different age groups (e.g. kids vs. adults).
* **Alternative providers:** integrate other image models (e.g. DALL·E or
  Midjourney) or text models (e.g. Anthropic’s Claude) by implementing
  alternative functions in `utils.py`.
* **One-click video assembly:** stitch images + voiceover + captions into an
  MP4 (server-side) or export an edit-friendly timeline (e.g., Premiere XML).
* **Brand presets:** reusable channel presets (tone, CTA wording, scene count,
  visual style) with saved profiles.

Contributions are welcome!  Feel free to fork this project and adapt it to your
own creative workflow.
