"use client";

import React, { useState, useEffect } from "react";
import { ChatMessage } from "@/types/agent";
import { Hotel } from "@/types/hotel";
import { sendAgentMessage, ApiError } from "@/lib/api";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { HotelDetailsModal } from "@/components/hotels/HotelDetailsModal";

export const ChatWindow: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [selectedHotel, setSelectedHotel] = useState<Hotel | null>(null);
  const [detailModalToken, setDetailModalToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [isDark, setIsDark] = useState<boolean>(false);

  // Initialize theme from localStorage or system preference
  useEffect(() => {
    try {
      const saved = localStorage.getItem("musafir_theme");
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      if (saved === "dark" || (!saved && prefersDark)) {
        setIsDark(true);
        document.documentElement.classList.add("dark");
      } else {
        setIsDark(false);
        document.documentElement.classList.remove("dark");
      }
    } catch {
      // Ignore if localStorage is unavailable
    }
  }, []);

  const toggleTheme = () => {
    setIsDark((prev) => {
      const next = !prev;
      if (next) {
        document.documentElement.classList.add("dark");
        try {
          localStorage.setItem("musafir_theme", "dark");
        } catch {}
      } else {
        document.documentElement.classList.remove("dark");
        try {
          localStorage.setItem("musafir_theme", "light");
        } catch {}
      }
      return next;
    });
  };

  const handleSendMessage = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isLoading) return;

    setError(null);

    // Optimistic user message
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const res = await sendAgentMessage(trimmed, conversationId);

      // Save/persist conversation_id across turns
      if (res.conversation_id) {
        setConversationId(res.conversation_id);
      }

      // Append assistant response with optional structured results
      const assistantMessage: ChatMessage = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: res.response,
        tool_calls: res.tool_calls || [],
        results: res.results || null,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      console.warn("Agent chat notice:", err);
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleResetSession = () => {
    setMessages([]);
    setConversationId(undefined);
    setSelectedHotel(null);
    setDetailModalToken(null);
    setError(null);
  };

  const handleSelectHotel = (hotel: Hotel) => {
    setSelectedHotel(hotel);
  };

  return (
    <div className="flex flex-col h-screen w-full bg-stone-50 dark:bg-stone-950 text-stone-900 dark:text-stone-100 transition-colors duration-200">
      {/* App Header */}
      <header className="h-16 px-4 sm:px-6 border-b border-stone-200 dark:border-stone-800 bg-white/90 dark:bg-stone-900/90 backdrop-blur-md flex items-center justify-between shrink-0 shadow-xs select-none">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-amber-500 to-amber-600 flex items-center justify-center text-white text-lg shadow-sm">
            🧭
          </div>
          <div>
            <h1 className="text-base font-bold tracking-tight text-stone-900 dark:text-stone-100 leading-none">
              Project Musafir
            </h1>
            <p className="text-[11px] font-medium text-stone-500 dark:text-stone-400 mt-0.5">
              AI Travel & Local Discovery Agent
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          {/* Active Selected Hotel Indicator */}
          {selectedHotel && (
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-200 border border-emerald-200 dark:border-emerald-800 text-xs font-semibold shadow-xs">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shrink-0" />
              <span className="truncate max-w-[120px] sm:max-w-[200px]" title={selectedHotel.name}>
                🏨 {selectedHotel.name}
              </span>
              <button
                type="button"
                onClick={() => setSelectedHotel(null)}
                className="ml-1 text-emerald-600 hover:text-emerald-900 dark:text-emerald-400 dark:hover:text-emerald-200 text-xs font-bold"
                title="Deselect hotel"
              >
                ✕
              </button>
            </div>
          )}

          {conversationId && (
            <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 text-xs font-mono">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span>Session: {conversationId.slice(0, 8)}...</span>
            </div>
          )}

          {/* Dark/Light Mode Toggle */}
          <button
            type="button"
            onClick={toggleTheme}
            className="p-1.5 sm:px-2 sm:py-1.5 rounded-lg border border-stone-200 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-600 dark:text-stone-300 hover:bg-stone-100 dark:hover:bg-stone-700 transition-colors flex items-center gap-1 text-xs font-medium cursor-pointer shadow-xs"
            title={isDark ? "Switch to light mode" : "Switch to dark mode"}
            aria-label="Toggle dark/light mode"
          >
            {isDark ? (
              <>
                <svg className="w-4 h-4 text-amber-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path
                    fillRule="evenodd"
                    d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z"
                    clipRule="evenodd"
                  />
                </svg>
                <span className="hidden sm:inline">Light</span>
              </>
            ) : (
              <>
                <svg className="w-4 h-4 text-stone-600 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
                </svg>
                <span className="hidden sm:inline">Dark</span>
              </>
            )}
          </button>

          <button
            onClick={handleResetSession}
            disabled={isLoading || (messages.length === 0 && !conversationId && !selectedHotel)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium border border-stone-200 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-200 hover:bg-stone-50 dark:hover:bg-stone-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer"
            title="Start a new travel session"
          >
            New Trip
          </button>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div className="px-4 py-2.5 bg-rose-50 border-b border-rose-200 text-rose-700 text-xs flex items-center justify-between animate-fadeIn">
          <div className="flex items-center gap-2 max-w-4xl mx-auto w-full">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              className="w-4 h-4 shrink-0 text-rose-500"
            >
              <path
                fillRule="evenodd"
                d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
                clipRule="evenodd"
              />
            </svg>
            <span className="font-medium">{error}</span>
            <button
              onClick={() => setError(null)}
              className="ml-auto text-rose-600 hover:text-rose-800 font-bold px-1"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Main Chat Stream */}
      <MessageList
        messages={messages}
        isLoading={isLoading}
        selectedHotel={selectedHotel}
        onSelectPrompt={handleSendMessage}
        onSelectHotel={handleSelectHotel}
        onViewDetails={setDetailModalToken}
      />

      {/* Chat Input Dock */}
      <ChatInput onSendMessage={handleSendMessage} isLoading={isLoading} />

      {/* Hotel Details Modal */}
      <HotelDetailsModal
        propertyToken={detailModalToken}
        selectedHotel={selectedHotel}
        onSelectHotel={handleSelectHotel}
        onClose={() => setDetailModalToken(null)}
      />
    </div>
  );
};
