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
* **Optional image generation** – generates real images using Google AI Studio image-capable
  Gemini models (e.g. `gemini-2.5-flash-image`). When image generation fails, the app
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
GEMINI_API_KEY = "AIza..."

# Optional overrides
openai_model = "gpt-4o-mini"
GOOGLE_AI_STUDIO_IMAGE_MODEL = "gemini-2.5-flash-image"
```

4. Run the app:

```bash
streamlit run app.py
```

Streamlit will start a local web server (usually at http://localhost:8501) where you can
enter a topic, pick settings and generate a script and images.  API keys are
loaded securely via Streamlit’s secrets manager and never exposed in your
source code.

## Node.js SDK quickstart (Gemini image output)

If you are calling Gemini from a Node.js backend and want native image output,
use the experimental model shown below:

```bash
npm install @google/generative-ai
```

```js
const { GoogleGenerativeAI } = require("@google/generative-ai");

const genAI = new GoogleGenerativeAI(process.env.API_KEY);
const model = genAI.getGenerativeModel({
  model: "gemini-2.0-flash-exp",
  generationConfig: {
    responseModalities: ["TEXT", "IMAGE"],
  },
});

async function generateFlashImage() {
  try {
    const prompt = "Create a digital art image of a futuristic city.";
    const result = await model.generateContent(prompt);
    const response = result.response;

    if (response.candidates && response.candidates[0].content.parts) {
      const parts = response.candidates[0].content.parts;
      for (const part of parts) {
        if (part.inlineData) {
          console.log("Image generated! Base64 length:", part.inlineData.data.length);
        } else if (part.text) {
          console.log("Text:", part.text);
        }
      }
    }
  } catch (error) {
    console.error("Generation failed:", error.message);
  }
}

generateFlashImage();
```

## Python SDK quickstart (Gemini 2.5 Flash Image)

```bash
pip install google-genai
```

```python
from google import genai
import base64
import os

def generate():
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
        http_options={"api_version": "v1beta"},
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents="INSERT_INPUT_HERE",
        config={"response_modalities": ["IMAGE"]},
    )

    if not response.candidates:
        print("No images generated.")
        return

    for i, candidate in enumerate(response.candidates):
        parts = getattr(candidate.content, "parts", [])
        for part in parts:
            if getattr(part, "inline_data", None) and getattr(part.inline_data, "data", None):
                image_bytes = base64.b64decode(part.inline_data.data)
                with open(f"generated_image_{i}.png", "wb") as f:
                    f.write(image_bytes)

if __name__ == "__main__":
    generate()
```

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

## Sora video API workflow (create → poll → download)

History Forge now uses the official OpenAI Sora Videos API endpoints on
`https://api.openai.com`:

- `POST /v1/videos` to create a video job
- `GET /v1/videos/{id}` to check status
- `GET /v1/models` in diagnostic mode to validate key scope

### Diagnostic CLI

```bash
python scripts/sora_health_check.py
```

If the script reports that `sora-2` / `sora-2-pro` are missing, your API key is
likely from a different org/project than the Sora-enabled one.

### Known-good curl example

```bash
curl https://api.openai.com/v1/videos \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sora-2",
    "prompt": "A cinematic aerial shot of a snowy mountain village at sunrise, drifting fog, ultra realistic.",
    "seconds": 4
  }'
```

### Required env vars / secrets checklist

- `OPENAI_API_KEY` (or `openai_api_key` in Streamlit secrets)
- `OPENAI_MODEL` (or `openai_model` in Streamlit secrets), for example `gpt-4o-mini`
- Key must belong to the same org/project where Sora is enabled
- Do **not** use `platform.openai.com` as an API base; use `https://api.openai.com`
- Use model names exactly: `sora-2` or `sora-2-pro`
- Never set `OPENAI_MODEL` to an API key (`sk-...`).

### Environment variable examples (local + Vercel)

Use the same variable names in local development and in Vercel Project Settings → Environment Variables:

```bash
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
```

Remove incorrect variants such as `MODEL=...` or `OPENAI_MODEL=sk-proj-...`.
