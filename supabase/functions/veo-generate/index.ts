const VEO_MODEL = "veo-2.0-generate-001";
const POLL_INTERVAL_MS = 8_000;
const MAX_POLLS = 90;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

// ---------------------------------------------------------------------------
// Service-account helpers — native Web Crypto, no npm dependencies
// ---------------------------------------------------------------------------

type ServiceAccount = {
  project_id?: string;
  client_email?: string;
  private_key?: string;
  token_uri?: string;
};

function loadServiceAccount(): Required<Pick<ServiceAccount, "project_id" | "client_email" | "private_key">> & ServiceAccount {
  const raw = Deno.env.get("GOOGLE_SERVICE_ACCOUNT_JSON");
  console.log("[auth] has_secret:", Boolean(raw), "[auth] secret_len:", raw?.length ?? 0);

  if (!raw) throw new Error("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret");

  let sa: ServiceAccount;
  try {
    sa = JSON.parse(raw);
  } catch (e) {
    throw new Error("GOOGLE_SERVICE_ACCOUNT_JSON invalid JSON (must be one-line minified). " + String(e));
  }

  if (!sa.project_id) throw new Error("Service account JSON missing project_id");
  if (!sa.client_email) throw new Error("Service account JSON missing client_email");
  if (!sa.private_key) throw new Error("Service account JSON missing private_key");

  // Normalize PEM: convert literal backslash-n sequences into real newlines
  const before = sa.private_key;
  const normalized = before.replace(/\\n/g, "\n").trim() + "\n";

  console.log(
    "[auth] email_present:", Boolean(sa.client_email),
    "[auth] key_has_BEGIN:", before.includes("BEGIN PRIVATE KEY"),
    "[auth] key_has_literal_slashn:", before.includes("\\n"),
    "[auth] normalized_has_newlines:", normalized.includes("\n"),
    "[auth] normalized_has_END:", normalized.includes("END PRIVATE KEY"),
  );

  sa.private_key = normalized;

  if (!sa.private_key.includes("-----BEGIN PRIVATE KEY-----") || !sa.private_key.includes("-----END PRIVATE KEY-----")) {
    throw new Error("private_key is not valid PEM after normalization. Re-check how the JSON was generated/pasted.");
  }

  return sa as any;
}

function base64urlEncode(data: Uint8Array | string): string {
  const bytes =
    typeof data === "string" ? new TextEncoder().encode(data) : data;
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function pemToPkcs8Bytes(pem: string): Uint8Array {
  const p = pem.trim();

  // Accept only PKCS#8 key format
  if (!p.includes("-----BEGIN PRIVATE KEY-----") || !p.includes("-----END PRIVATE KEY-----")) {
    // If you ever see RSA PRIVATE KEY here, the key is PKCS#1 and WebCrypto pkcs8 import will fail.
    throw new Error("Private key is not a PKCS#8 PEM (expected BEGIN/END PRIVATE KEY). Re-download the JSON key from GCP.");
  }

  // Remove markers
  let body = p
    .replace("-----BEGIN PRIVATE KEY-----", "")
    .replace("-----END PRIVATE KEY-----", "");

  // Strip whitespace first
  body = body.replace(/\s+/g, "");

  // Strip anything that is not base64 (this catches hidden chars/backslashes/etc.)
  const cleaned = body.replace(/[^A-Za-z0-9+/=]/g, "");

  if (cleaned.length < 1000) {
    throw new Error(`PEM body unexpectedly short after cleaning (${cleaned.length} chars). Secret may be corrupted.`);
  }

  // Fix base64 padding if needed
  const pad = cleaned.length % 4;
  const padded = pad === 0 ? cleaned : cleaned + "=".repeat(4 - pad);

  let binary: string;
  try {
    binary = atob(padded);
  } catch (e) {
    throw new Error("Failed to base64-decode PEM body. Secret likely contains invalid characters. " + String(e));
  }

  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

async function fetchAccessToken(scope: string, sa: Required<Pick<ServiceAccount, "project_id" | "client_email" | "private_key">> & ServiceAccount): Promise<string> {

  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const payload = {
    iss: sa.client_email,
    scope,
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  };

  const headerB64 = base64urlEncode(JSON.stringify(header));
  const payloadB64 = base64urlEncode(JSON.stringify(payload));
  const signingInput = `${headerB64}.${payloadB64}`;

  const pem = sa.private_key;

  const keyBytes = pemToPkcs8Bytes(pem);

  let cryptoKey: CryptoKey;
  try {
    cryptoKey = await crypto.subtle.importKey(
      "pkcs8",
      keyBytes,
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["sign"],
    );
  } catch (e) {
    throw new Error(
      "WebCrypto could not import the service account private key as PKCS#8. " +
      "This usually means the secret was pasted with corrupted characters or the key is not PKCS#8. " +
      "Re-download a fresh service account JSON key from GCP and re-paste the ONE-LINE minified JSON. " +
      "Import error: " + String(e),
    );
  }

  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    cryptoKey,
    new TextEncoder().encode(signingInput),
  );

  const jwt = `${signingInput}.${base64urlEncode(new Uint8Array(signature))}`;

  const tokenResp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: jwt,
    }),
  });

  if (!tokenResp.ok) {
    const text = await tokenResp.text();
    throw new Error(
      `Failed to obtain Google access token (${tokenResp.status}): ${text.slice(0, 200)}`,
    );
  }

  const tokenJson = await tokenResp.json();
  if (!tokenJson.access_token) {
    throw new Error("Google token endpoint did not return an access_token");
  }
  return tokenJson.access_token as string;
}

// ---------------------------------------------------------------------------
// Edge-function handler
// ---------------------------------------------------------------------------

Deno.serve(async (req) => {
  try {
    const sa = loadServiceAccount();

    if (req.method === "OPTIONS") {
      return new Response("ok", { headers: corsHeaders });
    }

    if (req.method !== "POST") {
      return new Response(JSON.stringify({ error: "Method not allowed" }), {
        status: 405,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const { prompt, aspectRatio } = await req.json();
    if (!prompt || typeof prompt !== "string") {
      return new Response(JSON.stringify({ error: "'prompt' is required" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const projectId = Deno.env.get("GOOGLE_CLOUD_PROJECT_ID") ?? sa.project_id;
    const location = Deno.env.get("GOOGLE_CLOUD_LOCATION") ?? "us-central1";
    if (!projectId) {
      throw new Error("Missing Supabase secret GOOGLE_CLOUD_PROJECT_ID");
    }

    const safeRatio = ["16:9", "9:16", "1:1"].includes(aspectRatio)
      ? aspectRatio
      : "16:9";

    const accessToken = await fetchAccessToken(
      "https://www.googleapis.com/auth/cloud-platform",
      sa,
    );

    const submitUrl =
      `https://${location}-aiplatform.googleapis.com/v1beta1/projects/${projectId}/locations/${location}/publishers/google/models/${VEO_MODEL}:predictLongRunning`;
    console.log("submitUrl:", submitUrl);

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
          durationSeconds: 8,
          sampleCount: 1,
        },
      }),
    });

    if (!submitResp.ok) {
      const text = await submitResp.text();
      throw new Error(
        `Veo submit failed (${submitResp.status}): ${text.slice(0, 500)}`,
      );
    }

    const submitJson = await submitResp.json();
    const operationName = submitJson.name;
    if (!operationName) {
      throw new Error("Veo did not return an operation name");
    }
    console.log("operationName:", operationName);

    const m = operationName.match(/\/locations\/([^/]+)\//);
    const opLocation = m?.[1] ?? location;
    const opUrl = `https://${opLocation}-aiplatform.googleapis.com/v1beta1/${operationName}`;
    for (let i = 0; i < MAX_POLLS; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      const opResp = await fetch(opUrl, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!opResp.ok) {
        const text = await opResp.text();
        throw new Error(
          `Veo poll failed (${opResp.status}): ${text.slice(0, 500)}`,
        );
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

      const inlineB64 =
        prediction.bytesBase64Encoded ?? prediction.videoData;
      if (inlineB64) {
        return new Response(JSON.stringify({ videoBase64: inlineB64 }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }

      const videoUri: string | undefined =
        prediction.videoUri ?? prediction.videoUrl;
      if (typeof videoUri === "string" && videoUri.startsWith("http")) {
        const dl = await fetch(videoUri);
        if (!dl.ok) {
          throw new Error(
            `Failed to download Veo result URL (${dl.status})`,
          );
        }
        const bytes = new Uint8Array(await dl.arrayBuffer());
        let binary = "";
        const chunkSize = 0x8000;
        for (
          let offset = 0;
          offset < bytes.length;
          offset += chunkSize
        ) {
          const chunk = bytes.subarray(
            offset,
            Math.min(offset + chunkSize, bytes.length),
          );
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
  } catch (err) {
    console.error("[fatal]", err instanceof Error ? err.stack ?? err.message : String(err));
    return new Response(
      JSON.stringify({ ok: false, error: String(err) }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      },
    );
  }
});
