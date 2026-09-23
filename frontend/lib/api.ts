/**
 * Project Musafir — API Client (Step 11)
 * Sends requests to FastAPI /agent/chat with structured error handling.
 */

import { AgentChatRequest, AgentChatResponse } from "@/types/agent";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Sends a message to the Musafir agent chat endpoint.
 */
export async function sendAgentMessage(
  message: string,
  conversationId?: string
): Promise<AgentChatResponse> {
  const trimmed = message.trim();
  if (!trimmed) {
    throw new ApiError("Please enter a valid message.", 422);
  }

  const payload: AgentChatRequest = {
    message: trimmed,
    ...(conversationId ? { conversation_id: conversationId } : {}),
  };

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/agent/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    // Network failure (e.g. backend server offline, connection refused, CORS failure)
    console.error("Network error communicating with Musafir backend:", error);
    throw new ApiError(
      "Unable to reach the Musafir server. Please check that the backend is running.",
      0
    );
  }

  if (!response.ok) {
    let detailMsg = "";
    try {
      const errJson = await response.json();
      detailMsg = errJson.detail || "";
    } catch {
      // Ignore JSON parse failure on non-JSON error responses
    }

    switch (response.status) {
      case 422:
        throw new ApiError("Please enter a valid message.", 422);
      case 404:
        throw new ApiError(
          "This conversation could not be found. Please start a new conversation.",
          404
        );
      case 429:
        throw new ApiError(
          "The travel agent is temporarily busy. Please try again shortly.",
          429
        );
      case 502:
        throw new ApiError(
          "The AI service is temporarily unavailable. Please try again.",
          502
        );
      case 504:
        throw new ApiError("The request took too long. Please try again.", 504);
      default:
        throw new ApiError(
          detailMsg || "An unexpected error occurred. Please try again.",
          response.status
        );
    }
  }

  try {
    const data: AgentChatResponse = await response.json();
    return data;
  } catch (parseErr) {
    console.error("Failed to parse agent response:", parseErr);
    throw new ApiError("Received an invalid response from the server.", 500);
  }
}
