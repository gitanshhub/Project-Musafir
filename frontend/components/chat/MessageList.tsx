import React, { useEffect, useRef } from "react";
import { ChatMessage } from "@/types/agent";
import { Hotel } from "@/types/hotel";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: ChatMessage[];
  isLoading: boolean;
  selectedHotel?: Hotel | null;
  onSelectPrompt: (prompt: string) => void;
  onSelectHotel?: (hotel: Hotel) => void;
  onViewDetails?: (propertyToken: string) => void;
}

const STARTER_PROMPTS = [
  {
    title: "Jaipur Getaway",
    prompt: "Plan a 3-day Jaipur trip",
    icon: "🏰",
  },
  {
    title: "Budget Hotels",
    prompt: "Find hotels in Manali under ₹5,000",
    icon: "🏔️",
  },
  {
    title: "Culture & Dining",
    prompt: "I want historical places and good food in Udaipur",
    icon: "🍛",
  },
];

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  isLoading,
  selectedHotel,
  onSelectPrompt,
  onSelectHotel,
  onViewDetails,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      {/* Empty / Welcome State */}
      {messages.length === 0 && (
        <div className="max-w-2xl mx-auto my-auto py-12 text-center flex flex-col items-center justify-center">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-amber-500 to-amber-600 flex items-center justify-center text-white text-3xl shadow-lg shadow-amber-500/20 mb-4 select-none">
            🧭
          </div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-50 mb-2">
            Welcome to Project Musafir
          </h2>
          <p className="text-sm text-slate-600 dark:text-slate-400 max-w-md mx-auto mb-8 leading-relaxed">
            Your conversational AI travel companion. Tell me where you want to go, your dates, budget, or preferred sights to get started.
          </p>

          <div className="w-full">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-3 text-center">
              Suggested Trips
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {STARTER_PROMPTS.map((starter) => (
                <button
                  key={starter.title}
                  onClick={() => onSelectPrompt(starter.prompt)}
                  className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-amber-500/60 dark:hover:border-amber-500/60 hover:shadow-sm text-left transition-all group cursor-pointer"
                >
                  <div className="text-xl mb-1.5">{starter.icon}</div>
                  <div className="text-xs font-semibold text-slate-800 dark:text-slate-200 group-hover:text-amber-600 dark:group-hover:text-amber-400">
                    {starter.title}
                  </div>
                  <div className="text-[11px] text-slate-500 dark:text-slate-400 line-clamp-1 mt-0.5">
                    {starter.prompt}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Messages Feed */}
      {messages.map((msg) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          selectedHotel={selectedHotel}
          onSelectHotel={onSelectHotel}
          onViewDetails={onViewDetails}
        />
      ))}

      {/* Loading Indicator */}
      {isLoading && (
        <div className="flex gap-3 w-full max-w-3xl mx-auto my-3 justify-start">
          <div className="w-8 h-8 rounded-lg bg-amber-500/10 dark:bg-amber-400/20 border border-amber-500/30 flex items-center justify-center text-amber-600 dark:text-amber-400 font-bold text-xs shrink-0 select-none shadow-sm">
            M
          </div>
          <div className="flex flex-col items-start max-w-[80%]">
            <div className="text-[11px] font-medium text-slate-400 dark:text-slate-500 mb-1 px-1 select-none">
              Musafir
            </div>
            <div className="px-4 py-3 rounded-2xl rounded-bl-xs bg-white dark:bg-slate-800/90 text-slate-700 dark:text-slate-200 border border-slate-200/80 dark:border-slate-700/80 text-sm shadow-sm flex items-center gap-2">
              <span className="flex gap-1 items-center">
                <span className="w-2 h-2 rounded-full bg-amber-500 animate-bounce [animation-delay:-0.3s]" />
                <span className="w-2 h-2 rounded-full bg-amber-500 animate-bounce [animation-delay:-0.15s]" />
                <span className="w-2 h-2 rounded-full bg-amber-500 animate-bounce" />
              </span>
              <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">
                Musafir is planning...
              </span>
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
};
