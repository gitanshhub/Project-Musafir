/**
 * Project Musafir — Frontend TypeScript Contracts (Step 11)
 * Mirrors the backend FastAPI /agent/chat schema.
 */

import { Hotel } from "./hotel";

export interface AgentResults {
  hotels?: Hotel[];
}

export interface AgentChatRequest {
  conversation_id?: string;
  message: string;
}

export interface AgentChatResponse {
  conversation_id: string;
  response: string;
  tool_calls: string[];
  iterations: number;
  state_summary: Record<string, unknown> | string;
  results?: AgentResults | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: string[];
  results?: AgentResults | null;
  timestamp: Date;
}
