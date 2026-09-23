"use client";

import React, { useState } from "react";
import { Hotel } from "@/types/hotel";

interface HotelCardProps {
  hotel: Hotel;
  isSelected: boolean;
  onSelect: (hotel: Hotel) => void;
  onViewDetails: (propertyToken: string) => void;
}

export const HotelCard: React.FC<HotelCardProps> = ({
  hotel,
  isSelected,
  onSelect,
  onViewDetails,
}) => {
  const [imageError, setImageError] = useState(false);

  const hasRating = typeof hotel.rating === "number" && hotel.rating > 0;
  const hasReviews = typeof hotel.review_count === "number" && hotel.review_count > 0;
  const hasPrice = typeof hotel.price_per_night === "number" && hotel.price_per_night > 0;
  const hasAmenities = Array.isArray(hotel.amenities) && hotel.amenities.length > 0;
  const hasToken = Boolean(hotel.property_token && hotel.property_token.trim());

  return (
    <div
      className={`flex flex-col justify-between bg-white dark:bg-stone-900 rounded-2xl border transition-all duration-200 overflow-hidden shadow-sm hover:shadow-md ${
        isSelected
          ? "border-emerald-500 ring-2 ring-emerald-500/20 bg-emerald-50/20 dark:bg-emerald-950/30"
          : "border-amber-100 dark:border-stone-800 hover:border-amber-300 dark:hover:border-amber-700"
      }`}
    >
      <div>
        {/* Thumbnail Image Container */}
        <div className="relative h-44 w-full bg-amber-50/60 dark:bg-stone-950 overflow-hidden border-b border-amber-100 dark:border-stone-800">
          {hotel.thumbnail && !imageError ? (
            <img
              src={hotel.thumbnail}
              alt={hotel.name}
              className="w-full h-full object-cover transition-transform duration-300 hover:scale-105"
              onError={() => setImageError(true)}
              loading="lazy"
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-amber-600/60 dark:text-amber-400/50 p-4">
              <svg
                className="w-10 h-10 mb-1.5 opacity-60"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="1.5"
                  d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"
                />
              </svg>
              <span className="text-xs font-medium text-amber-700/60 dark:text-amber-400/50">
                No hotel photo available
              </span>
            </div>
          )}

          {/* Selected Badge on Image */}
          {isSelected && (
            <div className="absolute top-3 right-3 bg-emerald-600 text-white text-xs font-semibold px-2.5 py-1 rounded-full shadow-md flex items-center gap-1">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" />
              </svg>
              Selected
            </div>
          )}
        </div>

        {/* Card Body */}
        <div className="p-4 space-y-2.5">
          {/* Hotel Name */}
          <h3
            className="font-semibold text-stone-900 dark:text-stone-100 text-base leading-snug line-clamp-2"
            title={hotel.name}
          >
            {hotel.name}
          </h3>

          {/* Rating & Reviews */}
          <div className="flex items-center gap-1.5 text-xs">
            {hasRating ? (
              <span className="font-semibold text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/60 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                <span>⭐</span>
                <span>{hotel.rating?.toFixed(1)}</span>
              </span>
            ) : (
              <span className="text-stone-600 dark:text-stone-400 font-medium">No rating</span>
            )}

            {hasReviews && (
              <span className="text-stone-600 dark:text-stone-400">
                · {hotel.review_count?.toLocaleString()} reviews
              </span>
            )}
          </div>

          {/* Price */}
          <div className="pt-0.5">
            {hasPrice ? (
              <div className="text-stone-900 dark:text-stone-100">
                <span className="text-lg font-bold tracking-tight">
                  ₹{Math.round(hotel.price_per_night!).toLocaleString()}
                </span>
                <span className="text-xs text-stone-600 dark:text-stone-400 font-normal"> / night</span>
              </div>
            ) : (
              <span className="text-xs text-stone-600 dark:text-stone-400 font-medium">Price unavailable</span>
            )}
          </div>

          {/* Amenities Pills */}
          {hasAmenities && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {hotel.amenities.slice(0, 3).map((amenity, idx) => (
                <span
                  key={idx}
                  className="text-[11px] bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 px-2 py-0.5 rounded-md font-medium truncate max-w-[130px]"
                >
                  {amenity}
                </span>
              ))}
              {hotel.amenities.length > 3 && (
                <span className="text-[10px] text-stone-600 dark:text-stone-400 self-center font-medium">
                  +{hotel.amenities.length - 3} more
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Action Footer */}
      <div className="p-4 pt-1 flex items-center gap-2 border-t border-stone-100 dark:border-stone-800 mt-2">
        <button
          type="button"
          onClick={() => {
            if (hasToken) {
              onViewDetails(hotel.property_token!);
            }
          }}
          disabled={!hasToken}
          className={`flex-1 text-xs font-semibold py-2 px-3 rounded-xl border transition-colors ${
            hasToken
              ? "text-stone-700 dark:text-stone-200 bg-white dark:bg-stone-800 border-stone-300 dark:border-stone-700 hover:bg-stone-50 dark:hover:bg-stone-750 active:bg-stone-100"
              : "text-stone-300 dark:text-stone-600 bg-stone-50 dark:bg-stone-900 border-stone-200 dark:border-stone-800 cursor-not-allowed"
          }`}
          title={hasToken ? "View hotel photos and full details" : "Details unavailable"}
        >
          View details
        </button>

        <button
          type="button"
          onClick={() => onSelect(hotel)}
          className={`flex-1 text-xs font-semibold py-2 px-3 rounded-xl transition-all cursor-pointer ${
            isSelected
              ? "bg-emerald-600 text-white shadow-sm hover:bg-emerald-700"
              : "bg-amber-600 text-white shadow-sm hover:bg-amber-700 active:bg-amber-800"
          }`}
        >
          {isSelected ? "Selected ✓" : "Select hotel"}
        </button>
      </div>
    </div>
  );
};
