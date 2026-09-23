"use client";

import React, { useState } from "react";
import { Hotel } from "@/types/hotel";
import { HotelCard } from "./HotelCard";

interface HotelResultsProps {
  hotels: Hotel[];
  selectedHotel: Hotel | null;
  onSelectHotel: (hotel: Hotel) => void;
  onViewDetails: (propertyToken: string) => void;
}

const INITIAL_LIMIT = 6;

export const HotelResults: React.FC<HotelResultsProps> = ({
  hotels,
  selectedHotel,
  onSelectHotel,
  onViewDetails,
}) => {
  const [showAll, setShowAll] = useState(false);

  if (!Array.isArray(hotels) || hotels.length === 0) {
    return null;
  }

  const displayedHotels = showAll ? hotels : hotels.slice(0, INITIAL_LIMIT);
  const hasMore = hotels.length > INITIAL_LIMIT;

  return (
    <div className="w-full mt-4 pt-3 border-t border-amber-100/80 dark:border-stone-800">
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold text-stone-800 dark:text-stone-100">
            Recommended Accommodations
          </span>
          <span className="bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 text-xs font-bold px-2 py-0.5 rounded-full">
            {hotels.length} {hotels.length === 1 ? "option" : "options"}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {displayedHotels.map((hotel, index) => {
          const isSelected = Boolean(
            selectedHotel &&
              ((hotel.property_token &&
                selectedHotel.property_token === hotel.property_token) ||
                selectedHotel.name.trim().toLowerCase() ===
                  hotel.name.trim().toLowerCase())
          );

          return (
            <HotelCard
              key={hotel.property_token || `${hotel.name}-${index}`}
              hotel={hotel}
              isSelected={isSelected}
              onSelect={onSelectHotel}
              onViewDetails={onViewDetails}
            />
          );
        })}
      </div>

      {hasMore && (
        <div className="mt-4 text-center">
          <button
            type="button"
            onClick={() => setShowAll(!showAll)}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-amber-800 dark:text-amber-200 bg-amber-50 dark:bg-stone-800 border border-amber-200 dark:border-stone-700 rounded-full hover:bg-amber-100 dark:hover:bg-stone-700 transition-colors shadow-sm cursor-pointer"
          >
            {showAll ? (
              <>Show top 6 hotels ↑</>
            ) : (
              <>Show all {hotels.length} hotels ({hotels.length - INITIAL_LIMIT} more) ↓</>
            )}
          </button>
        </div>
      )}
    </div>
  );
};
