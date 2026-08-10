export type JourneySample = {
  index: number;
  local: number;
};

export type LogoOrbitInput = "idle" | "keyboard" | "pointer" | "touch";

export type LogoOrbitState = {
  dragging: boolean;
  hasInteracted: boolean;
  input: LogoOrbitInput;
  lastInputAt: number;
  pitch: number;
  pitchVelocity: number;
  settleToStory: boolean;
  shotIndex: number;
  yaw: number;
  yawVelocity: number;
};

export const journeyShotIds = [
  "identity",
  "ready",
  "receive",
  "understand",
  "act",
  "remember",
] as const;

export type JourneyShotId = (typeof journeyShotIds)[number];
