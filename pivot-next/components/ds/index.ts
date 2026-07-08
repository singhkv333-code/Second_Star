/**
 * Pivot design system — barrel.
 *
 * import { Display, MonoTag, Panel, AgentCard, … } from "@/components/ds";
 */

export {
  Display,
  Title,
  Prose,
  Eyebrow,
  MonoTag,
  StatusPill,
  PillButton,
  Delta,
  Figure,
  Hairline,
  type AgentState,
  type MonoTagTone,
} from "./primitives";

export {
  Panel,
  MetricStat,
  SparkLine,
  MiniTable,
  SectionShell,
  CTABand,
  type PanelVariant,
} from "./surfaces";

export { ChatBubble, PromptChip, ChatInputBar, ThinkingTicker } from "./chat";

export { AgentCard, WorkflowStep, type StepKind } from "./agents";

export {
  TickerTape,
  ChainFlow,
  CandlePulse,
  BlueprintTile,
  DotDrift,
  ScanTile,
  type TickerItem,
  type ChainNode,
} from "./patterns";

export { MacWindow } from "./mockups";

export {
  StockSnapshotWidget,
  PayoffWidget,
  BacktestWidget,
  type SnapshotReturns,
  type PayoffSpec,
  type BacktestMetric,
} from "./widgets";
