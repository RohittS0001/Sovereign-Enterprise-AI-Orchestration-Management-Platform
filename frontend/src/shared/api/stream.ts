import { rawRequest } from "./client";
import type { AgentRun, AgentStep } from "./types";

export type AgentEvent =
  | { type: "run_started"; run_id: string }
  | { type: "step"; step: Omit<AgentStep, "id"> & { step_id: string } }
  | { type: "phase"; phase: string }
  | { type: "done"; run: AgentRun }
  | { type: "error"; message: string };

/** Split a Server-Sent Events buffer into complete events plus the unparsed remainder. */
export function parseSseChunk(buffer: string): { events: AgentEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: AgentEvent[] = [];
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data: "))
      .map((line) => line.slice(6))
      .join("\n");
    if (data) events.push(JSON.parse(data) as AgentEvent);
  }
  return { events, rest };
}

/** POST a question and invoke onEvent for each streamed event. */
export async function streamAgentQuery(
  query: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await rawRequest("/agent/query/stream", { body: { query }, signal });
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Streaming is not supported by this browser");
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseChunk(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
  if (buffer.trim()) parseSseChunk(buffer + "\n\n").events.forEach(onEvent);
}
