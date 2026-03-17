import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }

  try {
    const {
      prompt,
      image_base64,
      image_mime_type = "image/png",
      aspect_ratio = "9:16",
      duration_seconds = 5,
    } = await req.json();

    if (!prompt || !image_base64) {
      return new Response(
        JSON.stringify({ error: "prompt and image_base64 are required" }),
        { status: 400, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    const projectId = Deno.env.get("GOOGLE_CLOUD_PROJECT");
    const location = Deno.env.get("VERTEX_AI_LOCATION") ?? "us-central1";
    const accessToken = Deno.env.get("GOOGLE_ACCESS_TOKEN");

    if (!projectId || !accessToken) {
      return new Response(
        JSON.stringify({ error: "GOOGLE_CLOUD_PROJECT and GOOGLE_ACCESS_TOKEN must be set" }),
        { status: 500, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // Submit image-to-video job to Vertex AI Veo 2
    const submitUrl =
      `https://${location}-aiplatform.googleapis.com/v1/projects/${projectId}/locations/${location}/publishers/google/models/veo-2.0-generate-001:predictLongRunning`;

    const submitResp = await fetch(submitUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        instances: [
          {
            prompt,
            image: {
              bytesBase64Encoded: image_base64,
              mimeType: image_mime_type,
            },
          },
        ],
        parameters: {
          aspectRatio: aspect_ratio,
          durationSeconds: duration_seconds,
          sampleCount: 1,
        },
      }),
    });

    if (!submitResp.ok) {
      const err = await submitResp.text();
      return new Response(
        JSON.stringify({ error: `Vertex AI submit failed: ${err}` }),
        { status: submitResp.status, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    const operation = await submitResp.json();
    const operationName: string = operation.name;

    // Poll until the operation completes (max 5 minutes)
    const pollUrl =
      `https://${location}-aiplatform.googleapis.com/v1/${operationName}`;
    const MAX_POLLS = 60;
    const POLL_INTERVAL_MS = 5000;

    let videoB64: string | null = null;

    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));

      const pollResp = await fetch(pollUrl, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });

      if (!pollResp.ok) {
        const err = await pollResp.text();
        return new Response(
          JSON.stringify({ error: `Polling failed: ${err}` }),
          { status: pollResp.status, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
        );
      }

      const result = await pollResp.json();

      if (result.done) {
        if (result.error) {
          return new Response(
            JSON.stringify({ error: result.error.message ?? "Veo generation failed" }),
            { status: 500, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
          );
        }

        // Extract base64 video from response
        const videos = result.response?.predictions?.[0]?.videos
          ?? result.response?.videos
          ?? [];

        if (videos.length === 0) {
          return new Response(
            JSON.stringify({ error: "Veo returned no video data", raw: result }),
            { status: 500, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
          );
        }

        videoB64 = videos[0].bytesBase64Encoded ?? videos[0].videoBase64 ?? null;
        break;
      }
    }

    if (!videoB64) {
      return new Response(
        JSON.stringify({ error: "Veo generation timed out after 5 minutes" }),
        { status: 504, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({ video_base64: videoB64 }),
      { status: 200, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
    );

  } catch (err) {
    return new Response(
      JSON.stringify({ error: String(err) }),
      { status: 500, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
    );
  }
});
