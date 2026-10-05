import os
import re

import psycopg
import requests


API_KEY = os.getenv("YOUTUBE_API_KEY")
BASE_URL = "https://www.googleapis.com/youtube/v3"

MAX_RESULTS = 10

# Para nuestro agente, cualquier video de 60 segundos
# o menos se tratará como video corto.
SHORT_MAX_SECONDS = 180



# =========================================================
# BASE DE DATOS
# =========================================================

def get_connection():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


# =========================================================
# CANALES ACTIVOS
# =========================================================

def get_active_channels():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    channel_name,
                    youtube_handle,
                    uploads_playlist_id

                FROM channels

                WHERE active = TRUE
                  AND uploads_playlist_id IS NOT NULL

                ORDER BY id
                """
            )

            return cur.fetchall()


# =========================================================
# CONVERTIR DURACIÓN ISO 8601 A SEGUNDOS
# =========================================================

def parse_duration(duration):

    if not duration:
        return None

    pattern = (
        r"PT"
        r"(?:(\d+)H)?"
        r"(?:(\d+)M)?"
        r"(?:(\d+)S)?"
    )

    match = re.fullmatch(
        pattern,
        duration
    )

    if not match:
        return None

    hours = int(
        match.group(1) or 0
    )

    minutes = int(
        match.group(2) or 0
    )

    seconds = int(
        match.group(3) or 0
    )

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


# =========================================================
# OBTENER DURACIONES
# =========================================================

def get_video_details(video_ids):

    if not video_ids:
        return {}

    response = requests.get(
        f"{BASE_URL}/videos",
        params={
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "key": API_KEY,
        },
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    details = {}

    for item in data.get(
        "items",
        []
    ):

        video_id = item["id"]

        duration_iso = (
            item
            .get(
                "contentDetails",
                {}
            )
            .get("duration")
        )

        duration_seconds = (
            parse_duration(
                duration_iso
            )
        )

        if duration_seconds is None:

            is_short = None

        else:

            is_short = (
                duration_seconds
                <= SHORT_MAX_SECONDS
            )

        details[video_id] = {
            "duration_seconds":
                duration_seconds,

            "is_short":
                is_short,
        }

    return details


# =========================================================
# VIDEOS RECIENTES
# =========================================================

def get_recent_videos(
    playlist_id
):

    response = requests.get(
        f"{BASE_URL}/playlistItems",
        params={
            "part":
                "snippet,contentDetails",

            "playlistId":
                playlist_id,

            "maxResults":
                MAX_RESULTS,

            "key":
                API_KEY,
        },
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    videos = []

    for item in data.get(
        "items",
        []
    ):

        snippet = item[
            "snippet"
        ]

        content = item[
            "contentDetails"
        ]

        videos.append(
            {
                "video_id":
                    content[
                        "videoId"
                    ],

                "title":
                    snippet.get(
                        "title",
                        ""
                    ),

                "description":
                    snippet.get(
                        "description",
                        ""
                    ),

                "published_at":
                    content.get(
                        "videoPublishedAt",
                        snippet.get(
                            "publishedAt"
                        ),
                    ),
            }
        )

    # =====================================================
    # CONSULTAR DURACIÓN DE LOS VIDEOS
    # =====================================================

    video_ids = [
        video["video_id"]
        for video in videos
    ]

    details = (
        get_video_details(
            video_ids
        )
    )

    for video in videos:

        video_detail = (
            details.get(
                video["video_id"],
                {}
            )
        )

        video[
            "duration_seconds"
        ] = video_detail.get(
            "duration_seconds"
        )

        video[
            "is_short"
        ] = video_detail.get(
            "is_short"
        )

    return videos


# =========================================================
# GUARDAR / ACTUALIZAR VIDEO
# =========================================================

def save_video(
    channel_id,
    video
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO videos
                (
                    channel_id,
                    youtube_video_id,
                    title,
                    description,
                    published_at,
                    duration_seconds,
                    is_short
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )

                ON CONFLICT
                    (youtube_video_id)

                DO UPDATE SET

                    title =
                        EXCLUDED.title,

                    description =
                        EXCLUDED.description,

                    published_at =
                        EXCLUDED.published_at,

                    duration_seconds =
                        EXCLUDED.duration_seconds,

                    is_short =
                        EXCLUDED.is_short

                RETURNING
                    id,
                    (xmax = 0) AS inserted
                """,
                (
                    channel_id,
                    video[
                        "video_id"
                    ],
                    video[
                        "title"
                    ],
                    video[
                        "description"
                    ],
                    video[
                        "published_at"
                    ],
                    video[
                        "duration_seconds"
                    ],
                    video[
                        "is_short"
                    ],
                ),
            )

            row = cur.fetchone()

        conn.commit()

    return (
        row[1]
        if row
        else False
    )


# =========================================================
# MAIN
# =========================================================

def main():

    channels = (
        get_active_channels()
    )

    print(
        f"Canales a revisar: "
        f"{len(channels)}"
    )

    print()

    total_new = 0

    for (
        channel_id,
        channel_name,
        handle,
        playlist_id,
    ) in channels:

        print(
            f"=== "
            f"{channel_name} "
            f"({handle}) ==="
        )

        try:

            videos = (
                get_recent_videos(
                    playlist_id
                )
            )

            new_count = 0

            for video in videos:

                inserted = save_video(
                    channel_id,
                    video
                )

                duration = (
                    video[
                        "duration_seconds"
                    ]
                )

                is_short = (
                    video[
                        "is_short"
                    ]
                )

                if is_short is True:

                    video_type = (
                        "SHORT"
                    )

                elif is_short is False:

                    video_type = (
                        "VIDEO"
                    )

                else:

                    video_type = (
                        "DESCONOCIDO"
                    )

                if inserted:

                    new_count += 1
                    total_new += 1

                    prefix = (
                        "[NUEVO]"
                    )

                else:

                    prefix = (
                        "[YA EXISTE]"
                    )

                print(
                    f"{prefix} "
                    f"{video['video_id']} "
                    f"| {video_type} "
                    f"| {duration}s "
                    f"| {video['title']}"
                )

            print(
                f"Revisados: "
                f"{len(videos)} | "
                f"Nuevos: "
                f"{new_count}"
            )

            print()

        except Exception as e:

            print(
                f"[ERROR] "
                f"{channel_name}: "
                f"{e}"
            )

            print()

    print(
        "--------------------------------"
    )

    print(
        "Total de videos nuevos:",
        total_new
    )


if __name__ == "__main__":
    main()

