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
};
type Artifact = { ts?: number | string; type: string; [k: string]: unknown };
type Payload = {
  id: string; title: string; project?: string | null; bucket?: string;
  host?: string; started_at?: string; ended_at?: string;
  active_seconds?: number; digest?: string; event_count?: number;
  events?: Event[]; artifacts?: Artifact[];
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
          active_seconds, digest, event_count, updated_at)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, now())
       on conflict (id) do update set
         title=excluded.title, project=excluded.project, bucket=excluded.bucket,
         host=excluded.host, started_at=excluded.started_at, ended_at=excluded.ended_at,
         active_seconds=excluded.active_seconds, digest=excluded.digest,
         event_count=excluded.event_count, updated_at=now()`,
      [s.id, s.title, s.project ?? null, bucket, s.host ?? null,
       s.started_at ?? null, s.ended_at ?? null, s.active_seconds ?? 0,
       s.digest ?? null, s.event_count ?? events.length]
    );

    // Replace children so re-pushing a session is idempotent.
    await client.query("delete from tp_event where session_id=$1", [s.id]);
    await client.query("delete from tp_artifact where session_id=$1", [s.id]);

    for (const e of events) {
      await client.query(
        `insert into tp_event
           (session_id, seq, ts, type, cwd, command, exit, duration, text, hash, prev_hash)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
        [s.id, e.seq, e.ts ?? null, e.type, e.cwd ?? null, e.command ?? null,
         e.exit ?? null, e.duration ?? null, e.text ?? null, e.hash ?? null, e.prev_hash ?? null]
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
