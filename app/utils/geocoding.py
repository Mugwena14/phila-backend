import httpx
from typing import Optional


async def geocode_address(address: str, city: str, province: str) -> Optional[tuple[float, float]]:
    """
    Returns (latitude, longitude) or None if geocoding fails.
    Uses Nominatim (OpenStreetMap) — free, no API key needed.
    """
    full_address = f"{address}, {city}, {province}, South Africa"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": full_address,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "za",
                },
                headers={"User-Agent": "PhilaHealthApp/1.0"},
                timeout=10.0,
            )
            data = response.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
            return None
        except Exception:
            return None