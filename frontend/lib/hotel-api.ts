/**
 * Project Musafir — Hotel Details API Client (Step 12)
 * Connects to GET /hotels/{property_token} with structured error handling.
 */

import { HotelDetail } from "@/types/hotel";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export class HotelApiError extends Error {
  constructor(
    message: string,
    public statusCode?: number,
    public userMessage: string = message
  ) {
    super(message);
    this.name = "HotelApiError";
  }
}

/**
 * Fetches comprehensive hotel details including photo gallery, address, and full amenities list.
 *
 * @param propertyToken Unique SerpApi property identifier
 */
export async function getHotelDetails(
  propertyToken: string
): Promise<HotelDetail> {
  const cleanToken = propertyToken?.trim();
  if (!cleanToken) {
    throw new HotelApiError(
      "Missing property token",
      400,
      "Invalid hotel token. Unable to load details."
    );
  }

  const endpoint = `${API_BASE_URL.replace(/\/+$/, "")}/hotels/${encodeURIComponent(cleanToken)}`;

  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
    });
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    throw new HotelApiError(
      `Network failure: ${errorMsg}`,
      0,
      "Unable to reach the Musafir server. Please check that the backend is running."
    );
  }

  if (!response.ok) {
    let errorDetail = "";
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || JSON.stringify(errJson);
    } catch {
      errorDetail = response.statusText;
    }

    if (response.status === 404) {
      throw new HotelApiError(
        errorDetail,
        404,
        "Hotel details could not be found. Please choose another hotel."
      );
    }

    if (response.status === 422) {
      throw new HotelApiError(
        errorDetail,
        422,
        "Invalid request parameters for hotel details."
      );
    }

    if (response.status === 502 || response.status === 503) {
      throw new HotelApiError(
        errorDetail,
        response.status,
        "We couldn't load the hotel details right now. Please try again."
      );
    }

    throw new HotelApiError(
      errorDetail,
      response.status,
      "We couldn't load the hotel details. Please try again."
    );
  }

  const data: HotelDetail = await response.json();
  return data;
}
