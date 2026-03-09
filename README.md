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


For canonical secrets setup examples, see `docs/SECRETS.md`.


## Voiceover providers

The app now supports two voiceover providers in both the **Voiceover** tab and the **Automation** tab:

- **ElevenLabs** (existing behavior, Voice ID-based)
- **OpenAI Text-to-Speech**

### Provider selection

- Pick **Voice Provider** = `ElevenLabs` to keep the existing Voice ID workflow.
- Pick **Voice Provider** = `OpenAI` to use OpenAI TTS model + voice selection.

### OpenAI TTS options

Supported OpenAI TTS models:

- `gpt-4o-mini-tts`
- `tts-1`
- `tts-1-hd`

Supported built-in OpenAI voices:

- `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`

OpenAI voiceover uses the Audio Speech API (`/v1/audio/speech`) and requests `response_format` (not `format`) with `mp3` as the default output.

`gpt-4o-mini-tts` supports optional instruction text for speaking style/tone (for example pacing, warmth, or delivery style). For `tts-1` and `tts-1-hd`, instruction text is omitted automatically.

### Output path

Generated voiceover audio is saved to the same canonical project output path used by the workflow:

- `data/projects/<project_id>/assets/audio/voiceover.mp3`

### Automation behavior

During automation, the voiceover step now reads your configured provider settings and logs which provider was used:

- ElevenLabs logs `provider=elevenlabs voice_id=...`
- OpenAI logs `provider=openai model=... voice=...`

Downstream timeline, subtitle, and render steps continue to use the same canonical voiceover file path.

## Automated workflow (deterministic + resumable)

The app now includes a hardened **Automation** tab designed for full pipeline runs that are deterministic, resumable, and recoverable without background workers.

### What is automated

Automation now runs in this exact order (starting from an existing script):

1. Generate voiceover first
2. Split script into the selected number of scenes
3. Auto-apply scene narrative/subtitle text
4. Generate prompts from scene title + excerpt + visual intent + narration, including selected image style and aspect ratio
5. Generate images
6. Optionally apply video effects (Ken Burns motion for image-based scenes)
7. Final compile using scene visuals, voiceover, optional subtitles, and optional background music

Automation intentionally stops at render and does **not** auto-upload to YouTube.

### New automation behaviors

- **Canonical naming**: scene media is normalized to stable file names (`assets/images/sNN.png`, `assets/videos/sNN.mp4`).
- **Durable scene metadata**: each scene gets metadata including index/id/title/excerpt/visual intent/prompt/duration/active media type and asset paths.
- **Fallback media selection**:
  - if scene video is missing, render falls back to the scene image
  - if music is missing, render continues without music
  - if AI video generation fails, image-based flow continues
  - if caption burn fails and captionless fallback is enabled, render continues without burned captions
- **Preflight checks** report actionable issues for missing images, missing voiceover, invalid timeline references, empty media, and stale/mismatched scene paths.

### Resume / retry / recovery

Use the Automation tab controls:

- **Run Full Workflow**: full deterministic pass
- **Resume Missing Steps**: skip completed artifacts and continue from missing outputs
- **Regenerate Missing Scene Assets**: repair canonical scene asset references and identify missing scene files only
- **Rebuild Timeline from Disk Truth**: reconstruct timeline from current scene/media state
- **Render Final Video**: render after preflight validation

### Project state files

Per project (`data/projects/<project_id>/`), automation uses:

- `workflow_state.json`: step statuses, retries, timestamps, last error
- `project_manifest.json`: canonical project-level paths
- `project_state.json`: user/project configuration payload
- `scenes.json`: durable scene records
- `assets/scene_meta/sNN.json`: per-scene canonical metadata
- `timeline.json`: timeline used for final render

### How to run full automation in the app

1. Launch app: `streamlit run app.py`
2. Choose/create a project.
3. Open **Automation** tab.
4. In **Automation**, set: aspect ratio, image style, number of scenes, video effects on/off, background music on/off + track selection (project/shared library), and subtitles on/off.
5. Click **Run Full Workflow**.
6. If interrupted or partial, click **Resume Missing Steps**.
7. If media references drift, click **Regenerate Missing Scene Assets** then **Rebuild Timeline from Disk Truth**.
8. Click **Render Final Video** to produce `data/projects/<project_id>/renders/final.mp4`.

## Automation tab workflow (updated)

The Automation tab now provides a live, step-by-step run experience for long jobs.

### Live progress and diagnostics

- A progress bar updates as each automation step transitions through `not_started`, `running`, `completed`, `skipped`, or `failed`.
- The UI shows the current step label (for example: `Running step 1 of 7: Voiceover`).
- A step checklist tracks: `voiceover`, `scenes`, `narrative`, `prompts`, `images`, `effects`, `render`.
- Recent `workflow.log` lines are shown directly in the tab while runs execute.
- The last error and final render path are displayed when available.

### Pre-run automation settings

Before running automation, the tab now persists these settings per project:

- Aspect Ratio (`16:9` or `9:16`)
- Visual Style (dropdown)
- Number of Scenes
- Video Effects (on/off)
- Subtitles (on/off)
- Background Music (on/off)
- Background Music Selection (project + shared library)
- Voice Provider (`ElevenLabs` or `OpenAI`)
- ElevenLabs Voice ID (when ElevenLabs is selected)
- OpenAI TTS Model / OpenAI Voice / optional speaking instructions (when OpenAI is selected)

### Visual style dropdown

Automation now uses a shared visual style list so style choices are consistent:

- Photorealistic cinematic
- Photorealistic
- Documentary still
- Vintage painted
- Oil painting
- Dramatic realism
- Black and white archival
- Stylized illustration
- Historical engraving
- Matte painting

### Voice ID fallback behavior

When voiceover is enabled, the workflow resolves Voice ID in this order:

1. Automation tab selected Voice ID
2. Saved user preference Voice ID
3. `DEFAULT_VOICE_ID`

The run logs which source was used, and the tab warns before execution if no valid voice could be resolved.

### Automation step order

Automation execution order is:

1. Voiceover
2. Scenes
3. Narrative
4. Prompts
5. Images
6. Effects
7. Render

Notes:
- Narrative generation still runs even when subtitles are off (prompt quality depends on it).
- If effects are off, motion effects are disabled in automated timeline/render metadata.
- If music is enabled, selected music is passed into render metadata with default mix ratio of 50% vs. voiceover.


## Automation modes for 60-second YouTube shorts

The **Automation** tab now supports two entry modes:

1. **Topic → 60s Short Video** (default)
   - Enter a topic (required) and optional angle/direction.
   - The app generates a dedicated short-form narration script (~130–170 words) using the `youtube_short_60s` script profile.
   - Then it runs the full pipeline in this order: `script → voiceover → scenes → narrative → prompts → images → effects → render`.

2. **Existing Script → Full Workflow**
   - Uses the existing project script and skips script generation.
   - Runs: `voiceover → scenes → narrative → prompts → images → effects → render`.

### Recommended defaults for Topic mode

When **Topic → 60s Short Video** is selected, Automation preloads short-form defaults (still editable):

- Aspect ratio: `9:16`
- Number of scenes: `8`
- Subtitles: on
- Video effects: on
- Background music: off (selectable)
- Visual style: project style (fallback: `Photorealistic cinematic`)

### 60-second script generation behavior

Short-form script generation is tuned for history voiceover:

- ~130–170 spoken words
- narration-first writing with strong opening hook
- clear middle progression and memorable closing line
- no markdown, no bullets, no scene labels, no production notes
- optimized for short-form retention and later scene splitting

### Running the new default topic-first flow

1. Open **Automation** tab.
2. Keep mode on **Topic → 60s Short Video**.
3. Enter a topic (and optional angle/direction).
4. Optionally adjust scenes, style, subtitles/effects/music, and voice provider.
5. Click **Run Full Workflow**.
6. Review final output in `data/projects/<project_id>/renders/final.mp4`.

Automation still stops at render and does not auto-upload to YouTube.
