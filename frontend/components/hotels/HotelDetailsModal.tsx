"use client";

import React, { useEffect, useState } from "react";
import { Hotel, HotelDetail } from "@/types/hotel";
import { getHotelDetails } from "@/lib/hotel-api";

interface HotelDetailsModalProps {
  propertyToken: string | null;
  selectedHotel: Hotel | null;
  onSelectHotel: (hotel: Hotel) => void;
  onClose: () => void;
}

export const HotelDetailsModal: React.FC<HotelDetailsModalProps> = ({
  propertyToken,
  selectedHotel,
  onSelectHotel,
  onClose,
}) => {
  const [detail, setDetail] = useState<HotelDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeImageIdx, setActiveImageIdx] = useState(0);
  const [firstImageLoaded, setFirstImageLoaded] = useState(false);

  useEffect(() => {
    if (!propertyToken) {
      setDetail(null);
      setError(null);
      setFirstImageLoaded(false);
      return;
    }

    let isMounted = true;
    const tOpen = typeof performance !== "undefined" ? performance.now() : Date.now();
    setLoading(true);
    setError(null);
    setActiveImageIdx(0);
    setFirstImageLoaded(false);

    getHotelDetails(propertyToken)
      .then((data) => {
        if (isMounted) {
          const tDetails = typeof performance !== "undefined" ? performance.now() : Date.now();
          console.info(`[HotelModal Perf] Modal open -> details visible: ${(tDetails - tOpen).toFixed(1)}ms (${data?.images?.length || 0} thumbnails ready)`);
          setDetail(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (isMounted) {
          setError(
            err?.userMessage ||
              "We couldn't load the hotel details. Please try again."
          );
          setLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [propertyToken]);

  // Handle ESC key to close modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  if (!propertyToken) {
    return null;
  }

  const isSelected = Boolean(
    detail &&
      selectedHotel &&
      (selectedHotel.property_token === detail.property_token ||
        selectedHotel.name.trim().toLowerCase() ===
          detail.name.trim().toLowerCase())
  );

  const images = detail?.images || [];
  const activeImage =
    (images[activeImageIdx]?.original || images[activeImageIdx]?.thumbnail) ?? undefined;

  const handleSelect = () => {
    if (!detail) return;
    const hotelObj: Hotel = {
      name: detail.name,
      rating: detail.rating,
      review_count: detail.review_count,
      price_per_night: detail.price_per_night,
      currency: detail.currency || "INR",
      latitude: detail.latitude,
      longitude: detail.longitude,
      thumbnail: images[0]?.thumbnail || null,
      amenities: detail.amenities,
      property_token: detail.property_token,
    };
    onSelectHotel(hotelObj);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-stone-900/60 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl max-h-[90vh] flex flex-col bg-white dark:bg-stone-900 rounded-3xl shadow-2xl overflow-hidden border border-amber-100 dark:border-stone-800"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-100 dark:border-stone-800 bg-amber-50/40 dark:bg-stone-850">
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold text-stone-900 dark:text-stone-100 truncate">
              {detail ? detail.name : "Hotel Details"}
            </span>
            {detail?.hotel_class && (
              <span className="text-xs bg-amber-200/80 dark:bg-amber-950/80 text-amber-900 dark:text-amber-300 font-semibold px-2 py-0.5 rounded-md shrink-0">
                {detail.hotel_class}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-stone-100 dark:hover:bg-stone-800 rounded-full transition-colors cursor-pointer"
            title="Close (Esc)"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Modal Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {loading && (
            <div className="flex flex-col items-center justify-center py-16 text-stone-500">
              <div className="w-9 h-9 border-3 border-amber-600 border-t-transparent rounded-full animate-spin mb-3"></div>
              <p className="text-sm font-medium">Loading hotel details...</p>
            </div>
          )}

          {error && !loading && (
            <div className="p-4 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900/60 rounded-2xl text-center space-y-3">
              <p className="text-sm text-red-700 dark:text-red-300 font-medium">{error}</p>
              <button
                type="button"
                onClick={() => {
                  setError(null);
                  setLoading(true);
                  getHotelDetails(propertyToken)
                    .then((data) => {
                      setDetail(data);
                      setLoading(false);
                    })
                    .catch((err) => {
                      setError(err?.userMessage || "Failed to load hotel details.");
                      setLoading(false);
                    });
                }}
                className="px-4 py-1.5 text-xs font-semibold bg-red-600 text-white rounded-xl hover:bg-red-700 transition-colors cursor-pointer"
              >
                Retry
              </button>
            </div>
          )}

          {detail && !loading && (
            <>
              {/* Photo Gallery (Hotel-Specific Only) */}
              {images.length > 0 && activeImage ? (
                <div className="space-y-2">
                  <div className="relative h-64 sm:h-72 w-full rounded-2xl overflow-hidden bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700">
                    <img
                      src={activeImage}
                      alt={detail.name}
                      className="w-full h-full object-cover transition-opacity duration-300"
                      loading="eager"
                      onLoad={() => {
                        if (!firstImageLoaded) {
                          setFirstImageLoaded(true);
                          console.info(`[HotelModal Perf] First image rendered and visible`);
                        }
                      }}
                    />
                  </div>

                  {images.length > 1 && (
                    <div className="flex gap-2 overflow-x-auto pb-1">
                      {images.map((img, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => setActiveImageIdx(idx)}
                          className={`relative h-16 w-20 rounded-xl overflow-hidden shrink-0 border-2 transition-all cursor-pointer ${
                            activeImageIdx === idx
                              ? "border-amber-600 ring-2 ring-amber-600/30"
                              : "border-transparent opacity-70 hover:opacity-100"
                          }`}
                        >
                          <img
                            src={img.thumbnail || img.original || ""}
                            alt={`Photo ${idx + 1}`}
                            className="w-full h-full object-cover"
                            loading="lazy"
                          />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-10 bg-amber-50/40 dark:bg-stone-800/40 rounded-2xl border border-dashed border-amber-200 dark:border-stone-700 text-amber-700/60 dark:text-amber-400/50">
                  <svg className="w-10 h-10 mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                  <p className="text-xs font-medium">No hotel images available</p>
                </div>
              )}

              {/* Rating, Reviews & Address */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-sm">
                  {detail.rating ? (
                    <span className="font-semibold text-amber-800 dark:text-amber-300 bg-amber-100 dark:bg-amber-950/60 px-2 py-0.5 rounded-lg flex items-center gap-1">
                      <span>⭐</span>
                      <span>{detail.rating.toFixed(1)}</span>
                    </span>
                  ) : (
                    <span className="text-stone-600 dark:text-stone-400">No rating</span>
                  )}

                  {detail.review_count && (
                    <span className="text-stone-600 dark:text-stone-400">
                      ({detail.review_count.toLocaleString()} reviews)
                    </span>
                  )}
                </div>

                {detail.address && (
                  <p className="text-xs text-stone-600 dark:text-stone-300 flex items-start gap-1 pt-0.5">
                    <span className="text-amber-700 dark:text-amber-400 shrink-0 mt-0.5">📍</span>
                    <span>{detail.address}</span>
                  </p>
                )}
              </div>

              {/* Pricing & Times Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-3.5 bg-stone-50 dark:bg-stone-800/60 rounded-2xl border border-stone-200/70 dark:border-stone-700">
                <div>
                  <span className="block text-[11px] text-stone-500 dark:text-stone-400 uppercase tracking-wide font-medium">
                    Nightly Rate
                  </span>
                  <span className="text-sm font-bold text-stone-900 dark:text-stone-100">
                    {detail.price_per_night
                      ? `₹${Math.round(detail.price_per_night).toLocaleString()}`
                      : "Unavailable"}
                  </span>
                </div>

                <div>
                  <span className="block text-[11px] text-stone-500 dark:text-stone-400 uppercase tracking-wide font-medium">
                    Total Estimated
                  </span>
                  <span className="text-sm font-bold text-stone-900 dark:text-stone-100">
                    {detail.total_price
                      ? `₹${Math.round(detail.total_price).toLocaleString()}`
                      : "—"}
                  </span>
                </div>

                <div>
                  <span className="block text-[11px] text-stone-500 dark:text-stone-400 uppercase tracking-wide font-medium">
                    Check-in
                  </span>
                  <span className="text-xs font-semibold text-stone-800 dark:text-stone-200">
                    {detail.check_in_time || "After 14:00"}
                  </span>
                </div>

                <div>
                  <span className="block text-[11px] text-stone-500 dark:text-stone-400 uppercase tracking-wide font-medium">
                    Check-out
                  </span>
                  <span className="text-xs font-semibold text-stone-800 dark:text-stone-200">
                    {detail.check_out_time || "Before 11:00"}
                  </span>
                </div>
              </div>

              {/* Description */}
              {detail.description && (
                <div className="space-y-1">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                    About this hotel
                  </h4>
                  <p className="text-xs text-stone-700 dark:text-stone-300 leading-relaxed">
                    {detail.description}
                  </p>
                </div>
              )}

              {/* Amenities */}
              {detail.amenities && detail.amenities.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                    Amenities & Services
                  </h4>
                  <div className="flex flex-wrap gap-1.5">
                    {detail.amenities.map((amenity, idx) => (
                      <span
                        key={idx}
                        className="text-xs bg-amber-50 dark:bg-amber-950/50 text-amber-900 dark:text-amber-300 border border-amber-200/60 dark:border-amber-800/60 px-2.5 py-1 rounded-xl font-medium"
                      >
                        ✓ {amenity}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Modal Footer */}
        {detail && !loading && (
          <div className="flex items-center justify-between px-6 py-4 border-t border-stone-100 dark:border-stone-800 bg-stone-50 dark:bg-stone-850">
            <div>
              {detail.price_per_night && (
                <div className="text-stone-900 dark:text-stone-100">
                  <span className="text-lg font-bold">
                    ₹{Math.round(detail.price_per_night).toLocaleString()}
                  </span>
                  <span className="text-xs text-stone-500 dark:text-stone-400"> / night</span>
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-xs font-semibold text-stone-600 dark:text-stone-300 hover:text-stone-800 dark:hover:text-stone-100 rounded-xl hover:bg-stone-200/60 dark:hover:bg-stone-700 transition-colors cursor-pointer"
              >
                Close
              </button>

              <button
                type="button"
                onClick={() => {
                  handleSelect();
                  onClose();
                }}
                className={`px-5 py-2 text-xs font-semibold rounded-xl shadow-sm transition-all cursor-pointer ${
                  isSelected
                    ? "bg-emerald-600 text-white hover:bg-emerald-700"
                    : "bg-amber-600 text-white hover:bg-amber-700 active:bg-amber-800"
                }`}
              >
                {isSelected ? "Selected ✓" : "Select this hotel"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
