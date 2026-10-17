import { Pool } from "pg";

const EXPERIENCE_LEVELS = new Set([
  "beginner",
  "intermediate",
  "experienced",
  "professional",
]);

let pool: Pool | undefined;

function waitlistPool(): Pool {
  if (pool) return pool;

  const configured = process.env.WAITLIST_DATABASE_URL || process.env.DATABASE_URL;
  if (!configured) {
    throw new Error("WAITLIST_DATABASE_URL is not configured");
  }

  // Pivot's existing SQLAlchemy URL includes a Python-driver suffix that the
  // Node Postgres client does not understand. A standard PostgreSQL URL passes
  // through unchanged, which is the shape to set on Vercel.
  const connectionString = configured.replace(
    /^postgresql\+psycopg2:\/\//,
    "postgresql://",
  );

  pool = new Pool({
    connectionString,
    max: 2,
    connectionTimeoutMillis: 8_000,
    idleTimeoutMillis: 10_000,
    allowExitOnIdle: true,
  });
  return pool;
}

function noStoreJson(body: object, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

export async function POST(request: Request): Promise<Response> {
  // Raised with the free-text field: a 2,000-character answer plus the other
  // three fields and JSON overhead does not fit in 4 KB, and a person who
  // wrote a considered paragraph would have been refused for it.
  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > 12_288) {
    return noStoreJson({ error: "request is too large" }, 413);
  }

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return noStoreJson({ error: "invalid request" }, 400);
  }

  const email = String(body.email || "").trim().toLowerCase().slice(0, 254);
  const fullName = String(body.name || "").trim().slice(0, 120);
  const experience = String(body.experience || "").trim().toLowerCase();
  // Optional, so an empty answer is stored as NULL rather than as an empty
  // string: "did not say" and "said nothing" read the same in a report, and
  // only one of them is true.
  const style = String(body.style || "").trim().slice(0, 2_000);

  if (!email.includes("@") || !email.split("@").at(-1)?.includes(".")) {
    return noStoreJson({ error: "enter a valid email address" }, 400);
  }
  if (!fullName) {
    return noStoreJson({ error: "enter your name" }, 400);
  }
  if (!EXPERIENCE_LEVELS.has(experience)) {
    return noStoreJson({ error: "choose your trading experience" }, 400);
  }

  try {
    await waitlistPool().query(
      `INSERT INTO charto_landing.waitlist_registrations
         (email, full_name, trading_experience, trading_style, source)
       VALUES ($1, $2, $3, $4, 'landing')
       ON CONFLICT (email) DO UPDATE SET
         full_name = EXCLUDED.full_name,
         trading_experience = EXCLUDED.trading_experience,
         -- A second signup that SKIPS the optional field must not erase what
         -- the person wrote the first time. COALESCE keeps the earlier answer
         -- unless this one actually carries a new paragraph.
         trading_style = COALESCE(EXCLUDED.trading_style,
                                  charto_landing.waitlist_registrations.trading_style),
         updated_at = NOW()`,
      [email, fullName, experience, style || null],
    );
    return noStoreJson({ ok: true });
  } catch {
    return noStoreJson({ error: "could not save your registration" }, 503);
  }
}
