"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import { ChatMessage } from "@/types/agent";
import { Hotel } from "@/types/hotel";
import { HotelResults } from "@/components/hotels/HotelResults";

interface MessageBubbleProps {
  message: ChatMessage;
  selectedHotel?: Hotel | null;
  onSelectHotel?: (hotel: Hotel) => void;
  onViewDetails?: (propertyToken: string) => void;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  selectedHotel = null,
  onSelectHotel = () => {},
  onViewDetails = () => {},
}) => {
  const isUser = message.role === "user";
  const hasHotels =
    !isUser &&
    Boolean(message.results?.hotels && message.results.hotels.length > 0);

  return (
    <div
      className={`flex gap-3 w-full mx-auto my-3 ${
        hasHotels ? "max-w-4xl" : "max-w-3xl"
      } ${isUser ? "justify-end" : "justify-start"}`}
    >
      {/* Assistant Avatar */}
      {!isUser && (
        <div className="w-8 h-8 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-700 font-bold text-xs shrink-0 select-none shadow-sm mt-0.5">
          M
        </div>
      )}

      <div
        className={`flex flex-col ${
          hasHotels
            ? "w-full max-w-full"
            : "max-w-[85%] sm:max-w-[78%]"
        } ${isUser ? "items-end" : "items-start"}`}
      >
        <div className="text-[11px] font-medium text-stone-500 mb-1 px-1 select-none">
          {isUser ? "You" : "Musafir"}
        </div>

        {/* Message bubble card */}
        <div
          className={`px-4 py-3 rounded-2xl text-sm leading-relaxed shadow-sm transition-all ${
            isUser
              ? "bg-amber-600 text-white rounded-br-xs"
              : "bg-white text-stone-800 border border-stone-200/80 rounded-bl-xs w-full"
          }`}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words">{message.content}</div>
          ) : (
            <div className="max-w-none text-stone-800 leading-relaxed break-words [&>p]:mb-2.5 [&>p:last-child]:mb-0 [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:mb-2.5 [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:mb-2.5 [&>li]:mb-1 [&>h1]:text-base [&>h1]:font-bold [&>h2]:text-sm [&>h2]:font-bold [&>h3]:text-xs [&>h3]:font-bold [&>code]:bg-stone-100 [&>code]:px-1 [&>code]:py-0.5 [&>code]:rounded [&>code]:text-xs [&>code]:font-mono [&>a]:text-amber-700 [&>a]:underline">
              <ReactMarkdown
                components={{
                  a: ({ node, ...props }) => (
                    <a {...props} target="_blank" rel="noopener noreferrer" />
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}

          {/* Tool Usage Chips (sanitized names only) */}
          {!isUser && message.tool_calls && message.tool_calls.length > 0 && (
            <div className="mt-3 pt-2.5 border-t border-stone-100 flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-[11px] font-medium text-stone-500">
                Used:
              </span>
              {message.tool_calls.map((tool, idx) => (
                <span
                  key={`${tool}-${idx}`}
                  className="px-2 py-0.5 rounded-md bg-amber-50 text-amber-800 font-mono text-[11px] border border-amber-200/80"
                >
                  • {tool}
                </span>
              ))}
            </div>
          )}

          {/* Structured Hotel Cards */}
          {hasHotels && message.results?.hotels && (
            <HotelResults
              hotels={message.results.hotels}
              selectedHotel={selectedHotel}
              onSelectHotel={onSelectHotel}
              onViewDetails={onViewDetails}
            />
          )}
        </div>
      </div>

      {/* User Avatar */}
      {isUser && (
        <div className="w-8 h-8 rounded-lg bg-stone-200 flex items-center justify-center text-stone-600 font-semibold text-xs shrink-0 select-none shadow-sm mt-0.5">
          U
        </div>
      )}
    </div>
  );
};

