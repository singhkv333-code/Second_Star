import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
  Easing,
} from "remotion";
import { colors } from "./theme";
import { LogoForm } from "./scenes/LogoForm";
import { AppShell } from "./scenes/AppShell";
import { ChatGreeting } from "./scenes/ChatGreeting";
import { Composer } from "./scenes/Composer";
import { UserBubble } from "./scenes/UserBubble";
import { Thinking } from "./scenes/Thinking";
import { SnapshotCard } from "./scenes/SnapshotCard";
import { WorkflowDraftCardScene } from "./scenes/WorkflowDraftCardScene";
import { Outro } from "./scenes/Outro";

// Master timeline (all values in global frames @ 30 fps).
//
//   0    LOGO_FORM             0..120
// 110    SHELL_START           110..900  (overlap with logo for handoff)
// 240    TYPING_START          composer cursor + chars stream
// 350    SEND_PULSE            user "submits"
// 358    USER_BUBBLE
// 380    THINKING (108f visible → all 3 witty phrases cycle)
// 488    SNAPSHOT_CARD         488..620
// 600    WORKFLOW_CARD         600..780  (climax — overlaps with snapshot exit)
// 740    LIVE_AGENT_ENTERS     right-rail slide-in
// 760    COUNTER_TICK          3 → 4
// 780    OUTRO_START           780..900
// 900    TOTAL

const LOGO_END = 120;
const SHELL_START = 110;
const TYPING_START_GLOBAL = 240;
const SEND_PULSE_FRAME = 350;
const USER_BUBBLE_AT = 358;
const THINKING_AT = 380;
const SNAPSHOT_AT = 488;
const SNAPSHOT_EXIT = 620;
const WORKFLOW_AT = 600;
const WORKFLOW_ACTIVATE_AT = 740; // shows Activated chip
const LIVE_AGENT_START = 740;
const LIVE_AGENT_END = 790;
const COUNTER_TICK_FRAME = 762;
const OUTRO_START = 780;
const TOTAL = 900;

// ─── Camera path ────────────────────────────────────────────────────
//
// One continuous push/pull line so transitions never read as cuts.
// Scale + translate produce a "camera" feel without nested compositions.

type CameraState = {
  scale: number;
  x: number;
  y: number;
  chromeBlur: number;
};

const cameraAt = (frame: number): CameraState => {
  // Stage A — shell zoom-in from logo handoff (110..175)
  const enterT = interpolate(frame, [SHELL_START, 175], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.22, 1, 0.36, 1),
  });
  if (frame < 175) {
    return {
      scale: interpolate(enterT, [0, 1], [0.18, 1]),
      x: interpolate(enterT, [0, 1], [-780, 0]),
      y: interpolate(enterT, [0, 1], [-480, 0]),
      chromeBlur: 0,
    };
  }

  // Stage B — push toward composer (240..360)
  const pushIn = interpolate(frame, [240, 360], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  // Stage C — pull back as user submits / thinking shows (360..488)
  const pullBack = interpolate(frame, [360, 470], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  // Stage D — push into snapshot card (SNAPSHOT_AT..600)
  const snapZoom = interpolate(frame, [510, 600], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  // Stage E — pull back from snapshot, push into workflow (600..720)
  const workflowZoom = interpolate(frame, [620, 720], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  // Stage F — pan to right rail for Active Agents reveal (720..780)
  const railPan = interpolate(frame, [720, 780], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.22, 1, 0.36, 1),
  });

  // Stage G — final pull wide for outro handoff (780..840)
  const pullWide = interpolate(frame, [780, 840], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  // Scale composition:
  //   identity → 1.08 (composer push) → back to 1.0 → 1.30 (snapshot)
  //   → back to ~1.05 → 1.30 (workflow) → 1.15 during rail pan
  //   → 0.92 pulled wide
  let scale =
    1 +
    pushIn * 0.08 * pullBack +
    snapZoom * 0.30 -
    workflowZoom * 0.30 * (1 - workflowZoom) + // small dip between zooms
    workflowZoom * 0.30 +
    railPan * (-0.15) -
    pullWide * 0.38;

  // Translate path:
  //   Y down toward composer, then up toward card centers; X shifts right
  //   to center cards (chat area is offset by sidebar).
  const composerY = pushIn * pullBack * 80;
  // Snapshot card center ≈ (656, 472) on screen (1920×1080) — shift by
  // (304, 68) to bring it to (960, 540).
  const snapOffsetX = snapZoom * 304;
  const snapOffsetY = snapZoom * 68;
  // Workflow card sits at same x/y baseline as snapshot, but slightly
  // taller — we push y a touch more.
  const wfOffsetX = workflowZoom * 304;
  const wfOffsetY = workflowZoom * 110;
  // Right rail center ≈ (1760, 540). To bring it to screen center we
  // shift -800 in x.
  const railOffsetX = railPan * -460;
  const pullWideY = pullWide * 40;

  // Compose offsets, with snapshot and workflow ramps inversely related
  // (snapshot fades out as workflow fades in).
  const snapWeight = 1 - workflowZoom;
  const x =
    snapOffsetX * snapWeight * (1 - pullWide) +
    wfOffsetX * workflowZoom * (1 - pullWide) +
    railOffsetX * (1 - pullWide);
  const y =
    composerY +
    snapOffsetY * snapWeight * (1 - pullWide) +
    wfOffsetY * workflowZoom * (1 - pullWide) +
    pullWideY;

  // Chrome blur — driven by which "card moment" we're in. Eases up
  // when a card pushes in, eases down when the camera pulls back.
  const blurFromSnap = interpolate(frame, [510, 580, 600, 620], [0, 0.55, 0.55, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const blurFromWorkflow = interpolate(
    frame,
    [640, 720, 740, 770],
    [0, 0.7, 0.7, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const chromeBlur = Math.max(blurFromSnap, blurFromWorkflow);

  return { scale, x, y, chromeBlur };
};

export const PivotShowcase: React.FC = () => {
  const frame = useCurrentFrame();
  const camera = cameraAt(frame);

  const chromeReveal = interpolate(frame, [120, 175], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  const greetingExit = interpolate(
    frame,
    [SEND_PULSE_FRAME - 6, SEND_PULSE_FRAME + 10],
    [0, 1],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.4, 0, 0.2, 1),
    },
  );

  const composerExit = interpolate(
    frame,
    [SEND_PULSE_FRAME + 4, SEND_PULSE_FRAME + 28],
    [0, 1],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.4, 0, 0.2, 1),
    },
  );

  // Snapshot card cross-fades out as the workflow card cross-fades in.
  const snapshotOpacity = interpolate(
    frame,
    [SNAPSHOT_AT, SNAPSHOT_AT + 16, SNAPSHOT_EXIT, SNAPSHOT_EXIT + 18],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Live agent slide-in progress
  const liveAgentEnter = interpolate(
    frame,
    [LIVE_AGENT_START, LIVE_AGENT_END],
    [0, 1],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.16, 1, 0.3, 1),
    },
  );
  const rightRailCount = frame >= COUNTER_TICK_FRAME ? 4 : 3;

  return (
    <AbsoluteFill style={{ background: colors.bgBase }}>
      {/* Scene 1 — Logo formation */}
      <Sequence durationInFrames={LOGO_END} layout="none">
        <LogoForm totalFrames={LOGO_END} />
      </Sequence>

      {/* Scenes 2-8 — App shell with chrome, chat, and card sequences */}
      <Sequence
        from={SHELL_START}
        durationInFrames={TOTAL - SHELL_START}
        layout="none"
      >
        <AbsoluteFill style={{ background: colors.bgBase }}>
          <div
            style={{
              position: "absolute",
              inset: 0,
              transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})`,
              transformOrigin: "center center",
            }}
          >
            <AppShell
              chromeReveal={chromeReveal}
              chromeBlur={camera.chromeBlur}
              rightRailCount={rightRailCount}
              liveAgent={{
                name: "RELIANCE Weekday Dip-Buy",
                enter: liveAgentEnter,
              }}
            >
              {/* Greeting + chips intro */}
              <ChatGreeting exitProgress={greetingExit} />

              {/* User bubble */}
              {frame >= USER_BUBBLE_AT && (
                <div
                  style={{
                    position: "absolute",
                    left: 56,
                    right: 56,
                    top: 96,
                  }}
                >
                  <UserBubbleWrapped
                    enterAt={USER_BUBBLE_AT}
                    text="Buy ₹10k of Reliance every Friday at 3:55 PM if it's down 1% or more"
                  />
                </div>
              )}

              {/* Thinking indicator — visible across all 3 phrase cycles
                  so the witty rotation reads on screen. */}
              {frame >= THINKING_AT && frame < SNAPSHOT_AT + 6 && (
                <div
                  style={{
                    position: "absolute",
                    left: 56,
                    right: 56,
                    top: 200,
                  }}
                >
                  <ThinkingWrapped enterAt={THINKING_AT} exitAt={SNAPSHOT_AT - 4} />
                </div>
              )}

              {/* Snapshot card */}
              {frame >= SNAPSHOT_AT && snapshotOpacity > 0.01 && (
                <div
                  style={{
                    position: "absolute",
                    left: 56,
                    top: 168,
                    opacity: snapshotOpacity,
                  }}
                >
                  <SnapshotCardWrapped enterAt={SNAPSHOT_AT} />
                </div>
              )}

              {/* Workflow draft card — the hero */}
              {frame >= WORKFLOW_AT && (
                <div
                  style={{
                    position: "absolute",
                    left: 56,
                    top: 110,
                  }}
                >
                  <WorkflowDraftSceneWrapped
                    enterAt={WORKFLOW_AT}
                    activated={frame >= WORKFLOW_ACTIVATE_AT}
                  />
                </div>
              )}

              {/* Composer */}
              <ComposerWrapped
                typeStartGlobal={TYPING_START_GLOBAL}
                sendPulseGlobal={SEND_PULSE_FRAME}
                exitProgress={composerExit}
              />
            </AppShell>
          </div>
        </AbsoluteFill>
      </Sequence>

      {/* Outro */}
      <Sequence
        from={OUTRO_START}
        durationInFrames={TOTAL - OUTRO_START}
        layout="none"
      >
        <Outro />
      </Sequence>
    </AbsoluteFill>
  );
};

// ─── Frame-local wrappers ───────────────────────────────────────────

const UserBubbleWrapped: React.FC<{ enterAt: number; text: string }> = ({
  enterAt,
  text,
}) => <UserBubble text={text} enterAt={enterAt - SHELL_START} />;

const ThinkingWrapped: React.FC<{ enterAt: number; exitAt?: number }> = ({
  enterAt,
  exitAt,
}) => (
  <Thinking
    enterAt={enterAt - SHELL_START}
    exitAt={exitAt !== undefined ? exitAt - SHELL_START : undefined}
  />
);

const SnapshotCardWrapped: React.FC<{ enterAt: number }> = ({ enterAt }) => (
  <SnapshotCard enterAt={enterAt - SHELL_START} width={680} />
);

const WorkflowDraftSceneWrapped: React.FC<{
  enterAt: number;
  activated: boolean;
}> = ({ enterAt, activated }) => (
  <WorkflowDraftCardScene
    enterAt={enterAt - SHELL_START}
    width={680}
    activated={activated}
  />
);

const ComposerWrapped: React.FC<{
  typeStartGlobal: number;
  sendPulseGlobal: number;
  exitProgress: number;
}> = ({ typeStartGlobal, sendPulseGlobal, exitProgress }) => (
  <Composer
    typeStart={typeStartGlobal - SHELL_START}
    sendPulseAt={sendPulseGlobal - SHELL_START}
    exitProgress={exitProgress}
  />
);
