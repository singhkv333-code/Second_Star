import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
} from "remotion";
import { theme } from "./theme";
import { Scene1_LogoIntro } from "./scenes/Scene1_LogoIntro";
import { Scene2_Tagline } from "./scenes/Scene2_Tagline";
import { Scene3_ChatInput } from "./scenes/Scene3_ChatInput";
import { Scene4_FullChatUI } from "./scenes/Scene4_FullChatUI";
import { Scene5_AgentBacktest } from "./scenes/Scene5_AgentBacktest";
import { Scene6_ActiveAgents } from "./scenes/Scene6_ActiveAgents";
import { Scene7_Outro } from "./scenes/Scene7_Outro";

// Scene start frames (24 s @ 30 fps = 720 frames)
const T1 = 0;
const T2 = 90;
const T3 = 180;
const T4 = 300;
const T5 = 420;
const T6 = 540;
const T7 = 630;
const TOTAL = 720;

const FADE = 6; // 6-frame cross-fade between scenes

// CrossFade lives INSIDE a <Sequence>, so useCurrentFrame() already
// returns the sequence-local frame. We only need the sequence's total
// span to position the fade-out window. Fade-in stays at 0..FADE; the
// outgoing edge anchors to span-FADE..span.
const CrossFade: React.FC<{
  span: number;
  children: React.ReactNode;
}> = ({ span, children }) => {
  const frame = useCurrentFrame();

  const fadeIn = interpolate(frame, [0, FADE], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [span - FADE, span], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = fadeIn * fadeOut;

  return (
    <AbsoluteFill style={{ opacity }}>
      {children}
    </AbsoluteFill>
  );
};

export const PivotLaunch: React.FC = () => {
  return (
    <AbsoluteFill style={{ background: theme.cream }}>
      <Sequence from={T1} durationInFrames={T2 - T1 + FADE} layout="none">
        <CrossFade span={T2 - T1 + FADE}>
          <Scene1_LogoIntro />
        </CrossFade>
      </Sequence>

      <Sequence from={T2} durationInFrames={T3 - T2 + FADE} layout="none">
        <CrossFade span={T3 - T2 + FADE}>
          <Scene2_Tagline />
        </CrossFade>
      </Sequence>

      <Sequence from={T3} durationInFrames={T4 - T3 + FADE} layout="none">
        <CrossFade span={T4 - T3 + FADE}>
          <Scene3_ChatInput />
        </CrossFade>
      </Sequence>

      <Sequence from={T4} durationInFrames={T5 - T4 + FADE} layout="none">
        <CrossFade span={T5 - T4 + FADE}>
          <Scene4_FullChatUI />
        </CrossFade>
      </Sequence>

      <Sequence from={T5} durationInFrames={T6 - T5 + FADE} layout="none">
        <CrossFade span={T6 - T5 + FADE}>
          <Scene5_AgentBacktest />
        </CrossFade>
      </Sequence>

      <Sequence from={T6} durationInFrames={T7 - T6 + FADE} layout="none">
        <CrossFade span={T7 - T6 + FADE}>
          <Scene6_ActiveAgents />
        </CrossFade>
      </Sequence>

      <Sequence from={T7} durationInFrames={TOTAL - T7} layout="none">
        <CrossFade span={TOTAL - T7}>
          <Scene7_Outro />
        </CrossFade>
      </Sequence>
    </AbsoluteFill>
  );
};

export const TOTAL_FRAMES = TOTAL;
