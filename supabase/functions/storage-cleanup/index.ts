import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type RetentionMap = Record<string, number>;

type StorageEntry = {
  name: string;
  id?: string;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown> | null;
};

type BucketCleanupResult = {
  retention_days: number;
  files_found_for_deletion: number;
  deleted: number;
  errors?: string[];
};

const RETENTION_DAYS: RetentionMap = {
  uploads: 7,
  videos: 7,
  "generated-videos": 3,
  "history-forge-scripts": 30,
  "history-forge-videos": 7,
  "history-forge-audio": 7,
  "history-forge-images": 7,
};

const LIST_PAGE_SIZE = 100;
const DELETE_BATCH_SIZE = 100;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function isAuthorized(req: Request, expectedSecret: string | undefined): boolean {
  if (!expectedSecret) {
    console.error("Missing STORAGE_CLEANUP_SECRET env var");
    return false;
  }

  const authHeader = req.headers.get("authorization") ?? "";
  const [scheme, token] = authHeader.split(" ");

  return scheme?.toLowerCase() === "bearer" && token === expectedSecret;
}

function resolveObjectTimestamp(item: StorageEntry): number | null {
  const source = item.created_at ?? item.updated_at;
  if (!source) return null;

  const parsed = Date.parse(source);
  if (Number.isNaN(parsed)) return null;

  return parsed;
}

async function listFilesRecursively(
  supabase: ReturnType<typeof createClient>,
  bucket: string,
  prefix = "",
): Promise<StorageEntry[]> {
  const files: StorageEntry[] = [];
  let offset = 0;

  while (true) {
    const { data, error } = await supabase.storage.from(bucket).list(prefix, {
      limit: LIST_PAGE_SIZE,
      offset,
      sortBy: { column: "name", order: "asc" },
    });

    if (error) {
      throw new Error(`Failed to list '${bucket}/${prefix || ""}': ${error.message}`);
    }

    const pageItems = data ?? [];
    if (pageItems.length === 0) {
      break;
    }

    for (const item of pageItems as StorageEntry[]) {
      const itemPath = prefix ? `${prefix}/${item.name}` : item.name;

      // Supabase list responses have null metadata for folder-like entries.
      if (!item.metadata) {
        const nestedFiles = await listFilesRecursively(supabase, bucket, itemPath);
        files.push(...nestedFiles);
        continue;
      }

      files.push({ ...item, name: itemPath });
    }

    if (pageItems.length < LIST_PAGE_SIZE) {
      break;
    }

    offset += LIST_PAGE_SIZE;
  }

  return files;
}

async function deleteInBatches(
  supabase: ReturnType<typeof createClient>,
  bucket: string,
  paths: string[],
): Promise<{ deleted: number; errors: string[] }> {
  let deleted = 0;
  const errors: string[] = [];

  for (let i = 0; i < paths.length; i += DELETE_BATCH_SIZE) {
    const chunk = paths.slice(i, i + DELETE_BATCH_SIZE);
    const { data, error } = await supabase.storage.from(bucket).remove(chunk);

    if (error) {
      errors.push(`Batch ${i / DELETE_BATCH_SIZE + 1}: ${error.message}`);
      continue;
    }

    deleted += data?.length ?? 0;
  }

  return { deleted, errors };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "authorization, content-type",
      },
    });
  }

  if (req.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed" }, 405);
  }

  const expectedSecret = Deno.env.get("STORAGE_CLEANUP_SECRET");
  if (!isAuthorized(req, expectedSecret)) {
    return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");

  if (!supabaseUrl || !serviceRoleKey) {
    return jsonResponse(
      { ok: false, error: "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY" },
      500,
    );
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey);
  const nowMs = Date.now();

  const results: Record<string, BucketCleanupResult> = {};

  for (const [bucket, retentionDays] of Object.entries(RETENTION_DAYS)) {
    const cutoffMs = nowMs - retentionDays * 24 * 60 * 60 * 1000;

    console.log(`Scanning bucket '${bucket}' with retention ${retentionDays} day(s)`);

    try {
      const allFiles = await listFilesRecursively(supabase, bucket);
      const pathsToDelete = allFiles
        .filter((item) => {
          const objectTime = resolveObjectTimestamp(item);
          return objectTime !== null && objectTime < cutoffMs;
        })
        .map((item) => item.name);

      const { deleted, errors } = await deleteInBatches(supabase, bucket, pathsToDelete);

      results[bucket] = {
        retention_days: retentionDays,
        files_found_for_deletion: pathsToDelete.length,
        deleted,
        ...(errors.length > 0 ? { errors } : {}),
      };

      console.log(
        `Bucket '${bucket}': found=${pathsToDelete.length}, deleted=${deleted}, errors=${errors.length}`,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);

      results[bucket] = {
        retention_days: retentionDays,
        files_found_for_deletion: 0,
        deleted: 0,
        errors: [message],
      };

      console.error(`Bucket '${bucket}' cleanup failed:`, message);
    }
  }

  return jsonResponse({ ok: true, results });
});
