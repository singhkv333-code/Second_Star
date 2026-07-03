"use client";

/**
 * VoiceInputButton — press-to-record mic for any text input.
 *
 * Browser-recording + translation flow: MediaRecorder captures an opus/aac
 * voice blob, POST /audio/transcribe (whisper-1 translations) turns it into
 * ENGLISH text — Hindi/Hinglish speech included — and `onTranscript` hands
 * the text to the host input.
 *
 * States:
 *   idle        — quiet mic, tertiary ink
 *   recording   — red pulsing mic; click again to stop (auto-stops at 60 s)
 *   busy        — spinner while the blob uploads + transcribes
 *   error       — red mic-off flash for a few seconds, tooltip carries the
 *                 reason (mic denied / transcription failed), then idle
 *
 * Renders nothing when MediaRecorder isn't available (SSR, ancient browsers)
 * so host layouts never carry a dead button.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff } from "lucide-react";
import { transcribeAudio } from "@/lib/api";
import { isError } from "@/lib/types";
import { cn } from "@/lib/utils";

type VoiceState = "idle" | "recording" | "busy" | "error";

interface VoiceInputButtonProps {
  /** Receives the transcribed English text once transcription succeeds. */
  onTranscript: (text: string) => void;
  /** Icon size in px (button box scales with it). Default 16. */
  size?: number;
  className?: string;
  "data-testid"?: string;
}

// Hard cap so a forgotten open mic can't run up an unbounded upload.
const MAX_RECORDING_MS = 60_000;
const ERROR_FLASH_MS = 3_000;

// Preference order: opus-in-webm (Chrome/Firefox), then whatever the
// browser offers (Safari only knows mp4/aac). Empty string = browser default.
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

function pickMimeType(): string {
  for (const candidate of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return "";
}

export function VoiceInputButton({
  onTranscript,
  size = 16,
  className,
  "data-testid": dataTestId,
}: VoiceInputButtonProps): React.ReactElement | null {
  const [state, setState] = useState<VoiceState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // MediaRecorder only exists in the browser — gate rendering post-mount so
  // SSR markup matches the first client paint.
  const [supported, setSupported] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const maxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    setSupported(
      typeof window !== "undefined" &&
        typeof window.MediaRecorder !== "undefined" &&
        !!navigator.mediaDevices?.getUserMedia,
    );
    return () => {
      unmountedRef.current = true;
      if (maxTimerRef.current) clearTimeout(maxTimerRef.current);
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
      recorderRef.current?.stream
        .getTracks()
        .forEach((track) => track.stop());
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  const releaseStream = useCallback((): void => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
    if (maxTimerRef.current) {
      clearTimeout(maxTimerRef.current);
      maxTimerRef.current = null;
    }
  }, []);

  const flashError = useCallback((message: string): void => {
    if (unmountedRef.current) return;
    setErrorMessage(message);
    setState("error");
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    errorTimerRef.current = setTimeout(() => {
      if (!unmountedRef.current) {
        setState("idle");
        setErrorMessage(null);
      }
    }, ERROR_FLASH_MS);
  }, []);

  const finishRecording = useCallback(
    async (mimeType: string): Promise<void> => {
      releaseStream();
      const blob = new Blob(chunksRef.current, {
        type: mimeType || "audio/webm",
      });
      chunksRef.current = [];
      if (blob.size === 0) {
        flashError("Nothing recorded — try again");
        return;
      }
      if (!unmountedRef.current) setState("busy");
      try {
        const result = await transcribeAudio(blob);
        if (unmountedRef.current) return;
        if (isError(result)) {
          flashError(result.error.message);
          return;
        }
        const text = result.data.text.trim();
        if (!text) {
          flashError("Couldn't hear anything — try again");
          return;
        }
        setState("idle");
        onTranscript(text);
      } catch {
        flashError("Voice transcription failed — try again");
      }
    },
    [flashError, onTranscript, releaseStream],
  );

  const startRecording = useCallback(async (): Promise<void> => {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      flashError("Microphone access denied — allow it in the browser");
      return;
    }
    if (unmountedRef.current) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    const mimeType = pickMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((track) => track.stop());
      flashError("Recording isn't supported in this browser");
      return;
    }

    chunksRef.current = [];
    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      void finishRecording(recorder.mimeType || mimeType);
    };

    streamRef.current = stream;
    recorderRef.current = recorder;
    recorder.start();
    setState("recording");

    maxTimerRef.current = setTimeout(() => {
      if (recorderRef.current?.state === "recording") {
        recorderRef.current.stop();
      }
    }, MAX_RECORDING_MS);
  }, [finishRecording, flashError]);

  const handleClick = useCallback((): void => {
    if (state === "busy") return;
    if (state === "recording") {
      recorderRef.current?.stop();
      return;
    }
    void startRecording();
  }, [startRecording, state]);

  if (!supported) return null;

  const recording = state === "recording";
  const busy = state === "busy";
  const failed = state === "error";

  const label = recording
    ? "Stop recording"
    : busy
      ? "Transcribing…"
      : (errorMessage ?? "Speak instead of typing");

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      aria-label={label}
      title={label}
      data-testid={dataTestId ?? "voice-input-btn"}
      data-state={state}
      className={cn(
        "inline-flex shrink-0 items-center justify-center",
        className,
      )}
      style={{
        width: size + 12,
        height: size + 12,
        background: recording
          ? "hsl(var(--destructive) / 0.12)"
          : "transparent",
        border: "none",
        borderRadius: "var(--radius-pill)",
        color:
          recording || failed
            ? "hsl(var(--destructive))"
            : "var(--text-tertiary)",
        cursor: busy ? "wait" : "pointer",
        transition:
          "color 0.18s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        if (state === "idle") {
          e.currentTarget.style.color = "var(--text-primary)";
        }
      }}
      onMouseLeave={(e) => {
        if (state === "idle") {
          e.currentTarget.style.color = "var(--text-tertiary)";
        }
      }}
    >
      {busy ? (
        <Loader2 size={size} strokeWidth={2} className="animate-spin" aria-hidden={true} />
      ) : failed ? (
        <MicOff size={size} strokeWidth={2} aria-hidden={true} />
      ) : (
        <Mic
          size={size}
          strokeWidth={2}
          className={recording ? "animate-pulse" : undefined}
          aria-hidden={true}
        />
      )}
    </button>
  );
}
