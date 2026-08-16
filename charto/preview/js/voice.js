/* Charto preview — voice input for the composer.
 *
 * Pivot's VoiceInputButton, in Charto's idiom and against Charto's server.
 * The chain is the same one that ships there and the same one the backend
 * mirrors: MediaRecorder captures an opus/aac blob, POST /audio/transcribe
 * hands it to Azure Speech fast-transcription on the Foundry key, and a
 * Devanagari transcript comes back rendered into English. Latin-script
 * Hinglish is left exactly as spoken — the agent reads it natively, and a
 * round trip through translation would only flatten it.
 *
 * States, and what each one is for:
 *   idle       a quiet mic
 *   recording  the button goes red and pulses; click again to stop. Hard cap
 *              at 60 s so a forgotten open mic cannot run up an upload
 *   busy       a spinner while the blob uploads and transcribes
 *   error      a struck-through mic for three seconds AND a toast, because a
 *              tooltip nobody hovers is a failure nobody sees — the exact bug
 *              this button had in Pivot
 *
 * The text is APPENDED to whatever is already in the composer rather than
 * replacing it: speaking is a way of adding to a half-typed question, and
 * silently eating what someone had already written is unforgivable. Nothing
 * is ever sent — the user still presses Enter, so a mis-transcription is
 * edited rather than asked.
 *
 * Renders nothing at all where MediaRecorder does not exist, so no layout
 * carries a dead control.
 */
"use strict";

const Voice = (() => {
  const MAX_MS = 60_000;          // a forgotten mic stops itself
  const ERROR_MS = 3_000;
  // opus-in-webm first (Chrome/Firefox), then whatever the browser has —
  // Safari only knows mp4/aac. Empty string means "browser's own default".
  const MIME = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

  const supported = () =>
    typeof MediaRecorder !== "undefined" &&
    !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  function pickMime() {
    for (const m of MIME) {
      try { if (MediaRecorder.isTypeSupported(m)) return m; } catch { /* older */ }
    }
    return "";
  }

  /** Wire a mic button to an input. `onText` receives the transcript. */
  function attach(btn, onText, opts) {
    if (!btn) return;
    if (!supported()) { btn.hidden = true; return; }
    btn.hidden = false;
    const api = (opts && opts.api) || "";
    let state = "idle", rec = null, stream = null, chunks = [],
        maxTimer = 0, errTimer = 0;

    const paint = (label) => {
      btn.dataset.state = state;
      // Pivot's three glyphs: a spinner while it uploads, a struck mic while
      // a failure is still on screen, the plain mic the rest of the time.
      btn.innerHTML = Icons.svg(
        state === "busy" ? "loader" : state === "error" ? "micOff" : "mic", "sm");
      btn.title = label || (state === "recording" ? "Stop recording"
        : state === "busy" ? "Transcribing…" : "Speak instead of typing");
      btn.setAttribute("aria-label", btn.title);
      btn.disabled = state === "busy";
    };

    const fail = (msg) => {
      state = "error";
      paint(msg);
      // Say it out loud. The panel's toast is the one the rest of the app
      // uses, so a voice failure reads like every other failure.
      try { (opts && opts.toast ? opts.toast : console.warn)(msg); } catch { /* no toast */ }
      clearTimeout(errTimer);
      errTimer = setTimeout(() => { state = "idle"; paint(); }, ERROR_MS);
    };

    const release = () => {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      stream = null; rec = null;
      clearTimeout(maxTimer); maxTimer = 0;
    };

    async function finish(mime) {
      release();
      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      chunks = [];
      if (!blob.size) return fail("Nothing recorded — try again");
      state = "busy"; paint();
      try {
        // The blob IS the body: one file, no other field, so a multipart
        // envelope would be a parser on the server for nothing.
        const r = await fetch(`${api}/audio/transcribe`, {
          method: "POST",
          headers: typeof Auth !== "undefined"
            ? Auth.headers({ "Content-Type": blob.type || "audio/webm" })
            : { "Content-Type": blob.type || "audio/webm" },
          body: blob,
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.error) {
          return fail(d.error || "Voice transcription failed — try again");
        }
        const text = String(d.text || "").trim();
        if (!text) return fail("Couldn't hear anything — try again");
        state = "idle"; paint();
        onText(text);
      } catch {
        fail("Voice transcription failed — try again");
      }
    }

    async function start() {
      let s;
      try {
        s = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        return fail("Microphone access denied — allow it in the browser");
      }
      const mime = pickMime();
      try {
        rec = mime ? new MediaRecorder(s, { mimeType: mime }) : new MediaRecorder(s);
      } catch {
        s.getTracks().forEach((t) => t.stop());
        return fail("Recording isn't supported in this browser");
      }
      chunks = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = () => finish(rec ? rec.mimeType || mime : mime);
      stream = s;
      rec.start();
      state = "recording"; paint();
      maxTimer = setTimeout(() => {
        if (rec && rec.state === "recording") rec.stop();
      }, MAX_MS);
    }

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      if (state === "busy") return;
      if (state === "recording") { if (rec) rec.stop(); return; }
      start();
    });
    paint();
  }

  return { attach, supported };
})();
