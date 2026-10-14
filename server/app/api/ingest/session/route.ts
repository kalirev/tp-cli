// Rev Vision ingest endpoint for tp deskflight sessions.
// Drop this at:  app/api/ingest/session/route.ts
//
// Env required:
//   DATABASE_URL      Postgres connection string
//   TP_INGEST_TOKEN   shared bearer token (match `tp config token <...>`)
//
// Deps: `pg`.  (npm i pg)   Runs on the Node.js runtime, not Edge.

import { NextRequest, NextResponse } from "next/server";
import { Pool } from "pg";

export const runtime = "nodejs";

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

type Event = {
  seq: number; ts?: string; type: string; cwd?: string;
  command?: string; exit?: number; duration?: number; text?: string;
  hash?: string; prev_hash?: string;
  // v0.3
  term?: string; origin?: "live" | "journal" | "history"; ts_end?: string; jhash?: string;
  output?: string; output_sha256?: string; output_bytes?: number;
  output_truncated?: boolean; fullscreen?: boolean;
};
type Artifact = { ts?: number | string; type: string; [k: string]: unknown };
type Payload = {
  id: string; title: string; project?: string | null; bucket?: string;
  host?: string; started_at?: string; ended_at?: string;
  active_seconds?: number; digest?: string; event_count?: number;
  events?: Event[]; artifacts?: Artifact[];
  // v0.3
  active_estimated_seconds?: number; origins?: Record<string, number> | null;
  prior_digests?: unknown[]; output_included?: boolean;
};

const BUCKETS = new Set(["writeups", "projects", "tech-feed", "archive"]);

function unauthorized() {
  return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
}

export async function POST(req: NextRequest) {
  const expected = process.env.TP_INGEST_TOKEN;
  if (!expected) {
    return NextResponse.json({ ok: false, error: "server missing TP_INGEST_TOKEN" }, { status: 500 });
  }
  const token = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (token !== expected) return unauthorized();

  let s: Payload;
  try {
    s = (await req.json()) as Payload;
  } catch {
    return NextResponse.json({ ok: false, error: "invalid json" }, { status: 400 });
  }
  if (!s.id || !s.title) {
    return NextResponse.json({ ok: false, error: "id and title are required" }, { status: 400 });
  }
  const bucket = BUCKETS.has(s.bucket || "") ? (s.bucket as string) : "archive";
  const events = s.events ?? [];
  const artifacts = s.artifacts ?? [];

  const client = await pool.connect();
  try {
    await client.query("begin");

    await client.query(
      `insert into tp_session
         (id, title, project, bucket, host, started_at, ended_at,
          active_seconds, digest, event_count,
          active_estimated_seconds, origins, prior_digests, output_included, updated_at)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14, now())
       on conflict (id) do update set
         title=excluded.title, project=excluded.project, bucket=excluded.bucket,
         host=excluded.host, started_at=excluded.started_at, ended_at=excluded.ended_at,
         active_seconds=excluded.active_seconds, digest=excluded.digest,
         event_count=excluded.event_count,
         active_estimated_seconds=excluded.active_estimated_seconds,
         origins=excluded.origins, prior_digests=excluded.prior_digests,
         output_included=excluded.output_included, updated_at=now()`,
      [s.id, s.title, s.project ?? null, bucket, s.host ?? null,
       s.started_at ?? null, s.ended_at ?? null, s.active_seconds ?? 0,
       s.digest ?? null, s.event_count ?? events.length,
       s.active_estimated_seconds ?? 0,
       s.origins ? JSON.stringify(s.origins) : null,
       JSON.stringify(s.prior_digests ?? []), !!s.output_included]
    );

    // Replace children so re-pushing a session is idempotent.
    await client.query("delete from tp_event where session_id=$1", [s.id]);
    await client.query("delete from tp_artifact where session_id=$1", [s.id]);

    for (const e of events) {
      await client.query(
        `insert into tp_event
           (session_id, seq, ts, type, cwd, command, exit, duration, text, hash, prev_hash,
            term, origin, ts_end, jhash, output, output_sha256, output_bytes,
            output_truncated, fullscreen)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)`,
        [s.id, e.seq, e.ts ?? null, e.type, e.cwd ?? null, e.command ?? null,
         e.exit ?? null, e.duration ?? null, e.text ?? null, e.hash ?? null, e.prev_hash ?? null,
         e.term ?? null, e.origin ?? null, e.ts_end ?? null, e.jhash ?? null,
         e.output ?? null, e.output_sha256 ?? null, e.output_bytes ?? null,
         e.output_truncated ?? null, e.fullscreen ?? null]
      );
    }
    for (const a of artifacts) {
      const ts = typeof a.ts === "number" ? new Date(a.ts * 1000).toISOString() : (a.ts ?? null);
      await client.query(
        `insert into tp_artifact (session_id, ts, type, data) values ($1,$2,$3,$4)`,
        [s.id, ts, a.type, JSON.stringify(a)]
      );
    }

    await client.query("commit");
  } catch (err) {
    await client.query("rollback");
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : "ingest failed" },
      { status: 500 }
    );
  } finally {
    client.release();
  }

  return NextResponse.json({
    ok: true,
    id: s.id,
    bucket,
    url: `/${bucket}/${s.id}`,
  });
}

// `tp dispose <id> --remote` - delete a pushed session (children cascade).
export async function DELETE(req: NextRequest) {
  const expected = process.env.TP_INGEST_TOKEN;
  if (!expected) {
    return NextResponse.json({ ok: false, error: "server missing TP_INGEST_TOKEN" }, { status: 500 });
  }
  const token = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (token !== expected) return unauthorized();
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ ok: false, error: "id is required" }, { status: 400 });
  const r = await pool.query("delete from tp_session where id=$1", [id]);
  if (r.rowCount === 0) return NextResponse.json({ ok: false, error: "not found" }, { status: 404 });
  return NextResponse.json({ ok: true, id, deleted: true });
}
