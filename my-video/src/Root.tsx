import "./index.css";
import { Composition } from "remotion";
import { PivotShowcase } from "./PivotShowcase";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="PivotShowcase"
        component={PivotShowcase}
        durationInFrames={900}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
