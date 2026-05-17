import { NextResponse } from "next/server";
import { promises as dns } from "node:dns";
import { createClient } from "@supabase/supabase-js";

export const runtime = "nodejs";

// Stricter than the basic `something@something.tld` shape — bounds the
// local-part length, disallows leading/trailing/consecutive dots, and
// requires a 2+ letter TLD with no digits at the end.
const EMAIL_RE =
  /^(?=.{1,64}@)[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?=.{1,255}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$/;

const DISPOSABLE_DOMAINS = new Set([
  "10minutemail.com",
  "10minutemail.net",
  "20minutemail.com",
  "mailinator.com",
  "mailinator.net",
  "guerrillamail.com",
  "guerrillamail.net",
  "guerrillamail.org",
  "guerrillamail.biz",
  "guerrillamailblock.com",
  "sharklasers.com",
  "grr.la",
  "yopmail.com",
  "trashmail.com",
  "trashmail.net",
  "throwawaymail.com",
  "getairmail.com",
  "tempmail.com",
  "temp-mail.org",
  "temp-mail.io",
  "tempmailo.com",
  "tempr.email",
  "dispostable.com",
  "fakeinbox.com",
  "maildrop.cc",
  "mintemail.com",
  "mohmal.com",
  "moakt.com",
  "mailnesia.com",
  "spambox.us",
  "spam4.me",
  "spambog.com",
  "throwaway.email",
  "discard.email",
  "emailondeck.com",
  "anonbox.net",
  "fakemail.net",
  "33mail.com",
  "inboxbear.com",
  "burnermail.io",
]);

async function hasMx(domain: string): Promise<boolean> {
  try {
    const records = await dns.resolveMx(domain);
    // Filter out the "null MX" RFC 7505 sentinel that signals "this
    // domain does not accept mail."
    const valid = records.filter(
      (r) => r.exchange && r.exchange !== "." && r.priority >= 0,
    );
    return valid.length > 0;
  } catch {
    return false;
  }
}

function getSupabaseAdmin() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new Error("Missing Supabase env vars on the server.");
  }
  return createClient(url, key, { auth: { persistSession: false } });
}

type Body = { email?: unknown };

export async function POST(req: Request) {
  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return NextResponse.json(
      { ok: false, code: "bad_request", message: "Invalid request body." },
      { status: 400 },
    );
  }

  const raw = typeof body.email === "string" ? body.email.trim() : "";
  const email = raw.toLowerCase();

  if (!email) {
    return NextResponse.json(
      { ok: false, code: "missing", message: "Email is required." },
      { status: 400 },
    );
  }

  if (!EMAIL_RE.test(email)) {
    return NextResponse.json(
      { ok: false, code: "invalid_format", message: "That email doesn't look right." },
      { status: 400 },
    );
  }

  const domain = email.split("@")[1] ?? "";

  if (DISPOSABLE_DOMAINS.has(domain)) {
    return NextResponse.json(
      {
        ok: false,
        code: "disposable",
        message: "Please use a real, non-disposable email.",
      },
      { status: 400 },
    );
  }

  const deliverable = await hasMx(domain);
  if (!deliverable) {
    return NextResponse.json(
      {
        ok: false,
        code: "undeliverable",
        message: "We couldn't verify that email domain. Try another address.",
      },
      { status: 400 },
    );
  }

  try {
    const supabase = getSupabaseAdmin();
    const userAgent = req.headers.get("user-agent");
    const { error } = await supabase.from("waitlist_signups").insert({
      email,
      source: "waitlist-landing",
      user_agent: userAgent,
    });

    if (error) {
      if (error.code === "23505") {
        return NextResponse.json(
          { ok: false, code: "duplicate", message: "You're already on the list." },
          { status: 409 },
        );
      }
      return NextResponse.json(
        { ok: false, code: "server_error", message: "Something went wrong. Please try again." },
        { status: 500 },
      );
    }

    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json(
      { ok: false, code: "server_error", message: "Something went wrong. Please try again." },
      { status: 500 },
    );
  }
}
