import os

import httpx


URLSCAN_API_KEY = os.getenv("URLSCAN_API_KEY")

URLSCAN_SCAN_URL = "https://urlscan.io/api/v1/scan"


async def submit_url_scan(url: str):

    headers = {
        "API-Key": URLSCAN_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "url": url,
        "visibility": "public"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            URLSCAN_SCAN_URL,
            headers=headers,
            json=payload,
            timeout=10
        )

        response.raise_for_status()

        return response.json()
