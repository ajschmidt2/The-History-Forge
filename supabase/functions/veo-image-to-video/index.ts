import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const LOCATION = Deno.env.get("GOOGLE_CLOUD_LOCATION") || "us-central1";
const PROJECT_ID = Deno.env.get("GOOGLE_CLOUD_PROJECT_ID") || "";
const SERVICE_ACCOUNT_JSON = Deno.env.get("GOOGLE_SERVICE_ACCOUNT_JSON") || "";

async function getAccessToken(): Promise<string> {
  const sa = JSON.parse(SERVICE_ACCOUNT_JSON);
  const now = Math.floor(Date.now() / 1000);
  const header = btoa(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = btoa(JSON.stringify({
    iss: sa.client_email,
    scope: "https://www.googleapis.com/auth/cloud-platform",
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  }));
  const signingInput = `${header}.${payload}`;
  const keyData = sa.private_key
    .replace("-----BEGIN PRIVATE KEY-----", "")
    .replace("-----END PRIVATE KEY-----", "")
    .replace(/\n/g, "");
  const binaryKey = Uint8Array.from(atob(keyData), (c) => c.charCodeAt(0));
  const cryptoKey = await crypto.subtle.importKey(
    "pkcs8", binaryKey.buffer,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false, ["sign"]
  );
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5", cryptoKey,
    new TextEncoder().encode(signingInput)
  );
  const jwt = `${signingInput}.${btoa(String.fromCharCode(...new Uint8Array(signature)))}`;
  const tokenResp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=${jwt}`,
  });
  const tokenData = await tokenResp.json();
  return tokenData.access_token;
}

serve(async (req) => {
  try {
    const { prompt, image_base64, image_mime_type, duration_seconds, aspect_ratio } = await req.json();

    if (!image_base64 || !image_mime_type) {
      return new Response(JSON.stringify({ error: "image_base64 and image_mime_type are required" }), { status: 400 });
    }

    const accessToken = await getAccessToken();
    const endpoint = `https://${LOCATION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${LOCATION}/publishers/google/models/veo-2.0-generate-exp:predictLongRunning`;

    const requestBody = {
      instances: [{
        prompt: prompt || "Animate this scene with natural cinematic motion, dramatic atmosphere, historically immersive.",
        image: {
          bytesBase64Encoded: image_base64,
          mimeType: image_mime_type,
        },
      }],
      parameters: {
        aspectRatio: aspect_ratio || "9:16",
        durationSeconds: duration_seconds || 5,
        sampleCount: 1,
        personGeneration: "allow_adult",
      },
    };

    // Submit the long-running operation
    const submitResp = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestBody),
    });

    if (!submitResp.ok) {
      const err = await submitResp.text();
      return new Response(JSON.stringify({ error: `Veo submit failed: ${err}` }), { status: 502 });
    }

    const operation = await submitResp.json();
    const operationName = operation.name;

    // Poll for completion (max 5 minutes)
    const pollEndpoint = `https://${LOCATION}-aiplatform.googleapis.com/v1/${operationName}`;
    const deadline = Date.now() + 300_000;

    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 10_000));
      const pollResp = await fetch(pollEndpoint, {
        headers: { "Authorization": `Bearer ${accessToken}` },
      });
      const pollData = await pollResp.json();
      if (pollData.done) {
        const videos = pollData.response?.predictions?.[0]?.videos || [];
        if (videos.length === 0) {
          return new Response(JSON.stringify({ error: "Veo returned no video" }), { status: 502 });
        }
        const videoBase64 = videos[0].bytesBase64Encoded;
        return new Response(JSON.stringify({ video_base64: videoBase64 }), {
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    return new Response(JSON.stringify({ error: "Veo timed out after 5 minutes" }), { status: 504 });

  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), { status: 500 });
  }
});
