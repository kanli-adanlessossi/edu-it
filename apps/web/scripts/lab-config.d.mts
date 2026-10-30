export const CONFIG_PY: string;
export interface ParsedLabClientConfig {
  gradingMinIntervalMs: number;
  warningThresholdMs: number;
  resultPollIntervalMs: number;
  provisionSlowMs: number;
  reconnectWindowMs: number;
  reconnectBackoffMs: number[];
}
export function parseLabClientConfig(source: string): ParsedLabClientConfig;
export function readLabClientConfig(configPath?: string): ParsedLabClientConfig;
