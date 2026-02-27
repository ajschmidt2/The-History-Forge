import { GoogleAuth } from "npm:google-auth-library@9";

const VEO_MODEL = "veo-2.0-generate-001";
const POLL_INTERVAL_MS = 8_000;
const MAX_POLLS = 90;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

function readServiceAccount(): Record<string, unknown> {
  const raw = Deno.env.get("GOOGLE_SERVICE_ACCOUNT_JSON") ?? "";
  if (!raw) {
    throw new Error("Missing Supabase secret GOOGLE_SERVICE_ACCOUNT_JSON");
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON");
  }
}

async function fetchAccessToken(scopes: string[]): Promise<string> {
  const credentials = readServiceAccount();
  const auth = new GoogleAuth({
    credentials,
    scopes,
  });

  const client = await auth.getClient();
  const token = await client.getAccessToken();
  if (!token.token) {
    throw new Error("Failed to mint Google access token from service account");
  }
  return token.token;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  try {
    const { prompt, aspectRatio } = await req.json();
    if (!prompt || typeof prompt !== "string") {
      return new Response(JSON.stringify({ error: "'prompt' is required" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const projectId = Deno.env.get("GOOGLE_CLOUD_PROJECT_ID") ?? "";
    const location = Deno.env.get("GOOGLE_CLOUD_LOCATION") ?? "us-central1";
    if (!projectId) {
      throw new Error("Missing Supabase secret GOOGLE_CLOUD_PROJECT_ID");
    }

    const safeRatio = ["16:9", "9:16", "1:1"].includes(aspectRatio) ? aspectRatio : "16:9";

    const accessToken = await fetchAccessToken([
      "https://www.googleapis.com/auth/cloud-platform",
    ]);

    const submitUrl = `https://${location}-aiplatform.googleapis.com/v1beta1/projects/${projectId}/locations/${location}/publishers/google/models/${VEO_MODEL}:predictLongRunning`;

    const submitResp = await fetch(submitUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        instances: [{ prompt }],
        parameters: {
          aspectRatio: safeRatio,
          videoDurationSeconds: 8,
          sampleCount: 1,
        },
      }),
    });

    if (!submitResp.ok) {
      const text = await submitResp.text();
      throw new Error(`Veo submit failed (${submitResp.status}): ${text.slice(0, 500)}`);
    }

    const submitJson = await submitResp.json();
    const operationName = submitJson.name;
    if (!operationName) {
      throw new Error("Veo did not return an operation name");
    }

    const opUrl = `https://${location}-aiplatform.googleapis.com/v1beta1/${operationName}`;
    for (let i = 0; i < MAX_POLLS; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      const opResp = await fetch(opUrl, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!opResp.ok) {
        const text = await opResp.text();
        throw new Error(`Veo poll failed (${opResp.status}): ${text.slice(0, 500)}`);
      }

      const opJson = await opResp.json();
      if (!opJson.done) {
        continue;
      }

      if (opJson.error) {
        throw new Error(opJson.error.message ?? "Veo generation failed");
      }

      const prediction = opJson.response?.predictions?.[0];
      if (!prediction) {
        throw new Error("Veo returned no predictions");
      }

      const inlineB64 = prediction.bytesBase64Encoded ?? prediction.videoData;
      if (inlineB64) {
        return new Response(JSON.stringify({ videoBase64: inlineB64 }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }

      const videoUrl = prediction.videoUrl;
      if (typeof videoUrl === "string" && videoUrl.startsWith("http")) {
        const dl = await fetch(videoUrl);
        if (!dl.ok) {
          throw new Error(`Failed to download Veo result URL (${dl.status})`);
        }
        const bytes = new Uint8Array(await dl.arrayBuffer());
        let binary = "";
        const chunkSize = 0x8000;
        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
          const chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
          binary += String.fromCharCode(...chunk);
        }
        const b64 = btoa(binary);
        return new Response(JSON.stringify({ videoBase64: b64 }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }

      throw new Error("Veo returned an unexpected prediction format");
    }

    throw new Error("Veo generation timed out");
  } catch (error) {
    return new Response(
      JSON.stringify({ error: error instanceof Error ? error.message : "Unknown error" }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      },
    );
  }
});
