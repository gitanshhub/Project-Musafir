/**
 * Project Musafir — Hotel Types (Step 12)
 * Normalized TypeScript models mirroring backend Hotel and HotelDetail schemas.
 */

export interface Hotel {
  name: string;
  rating?: number | null;
  review_count?: number | null;
  price_per_night?: number | null;
  currency?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  thumbnail?: string | null;
  amenities: string[];
  property_token?: string | null;
}

export interface HotelImage {
  thumbnail?: string | null;
  original?: string | null;
}

export interface HotelDetail {
  name: string;
  rating?: number | null;
  review_count?: number | null;
  hotel_class?: string | null;
  description?: string | null;
  address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  amenities: string[];
  images: HotelImage[];
  check_in_time?: string | null;
  check_out_time?: string | null;
  price_per_night?: number | null;
  total_price?: number | null;
  currency?: string;
  property_token: string;
}
