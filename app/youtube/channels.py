import os

import psycopg
import requests


API_KEY = os.getenv("YOUTUBE_API_KEY")
BASE_URL = "https://www.googleapis.com/youtube/v3"


def get_connection():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


def resolve_handle(handle):
    handle_clean = handle.lstrip("@")

    response = requests.get(
        f"{BASE_URL}/channels",
        params={
            "part": "snippet,contentDetails",
            "forHandle": handle_clean,
            "key": API_KEY,
        },
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("items"):
        return None

    channel = data["items"][0]

    return {
        "handle": handle,
        "channel_id": channel["id"],
        "name": channel["snippet"]["title"],
        "uploads_playlist":
            channel["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def update_channel(result):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE channels
                SET youtube_channel_id = %s,
                    uploads_playlist_id = %s,
                    channel_name = %s,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE youtube_handle = %s
                """,
                (
                    result["channel_id"],
                    result["uploads_playlist"],
                    result["name"],
                    result["handle"],
                ),
            )

        conn.commit()


def main():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT youtube_handle
                FROM channels
                WHERE active = TRUE
                ORDER BY id
                """
            )

            handles = [row[0] for row in cur.fetchall()]

    print(f"Canales activos: {len(handles)}")

    for handle in handles:

        try:

            result = resolve_handle(handle)

            if not result:
                print(f"[NO ENCONTRADO] {handle}")
                continue

            update_channel(result)

            print(
                f"[OK] {result['name']} | "
                f"{result['channel_id']} | "
                f"{result['uploads_playlist']}"
            )

        except Exception as e:

            print(f"[ERROR] {handle}: {e}")


if __name__ == "__main__":
    main()
