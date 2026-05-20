import "./index.css";
import { Composition, AbsoluteFill } from "remotion";
import { PivotLaunch, TOTAL_FRAMES } from "./PivotLaunch";
import { theme } from "./theme";

// 1920×1080 master video. The 1080×1920 social cut renders the same
// composition fit to 1080-wide and centered vertically on a cream
// backdrop. Pure scale + center keeps every scene readable on mobile
// without re-laying-out each one.

const VerticalPivotLaunch: React.FC = () => {
  const targetH = 1920;
  const stageW = 1920;
  const stageH = 1080;
  const targetW = 1080;
  const scale = targetW / stageW; // 0.5625

  return (
    <AbsoluteFill style={{ background: theme.cream }}>
      <div
        style={{
          position: "absolute",
          top: (targetH - stageH * scale) / 2,
          left: 0,
          width: stageW,
          height: stageH,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      >
        <PivotLaunch />
      </div>
    </AbsoluteFill>
  );
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="PivotLaunch"
        component={PivotLaunch}
        durationInFrames={TOTAL_FRAMES}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="PivotLaunchVertical"
        component={VerticalPivotLaunch}
        durationInFrames={TOTAL_FRAMES}
        fps={30}
        width={1080}
        height={1920}
      />
    </>
  );
};
