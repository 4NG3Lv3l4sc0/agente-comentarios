import os
import re
import json

import psycopg
import requests


# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.getenv("YOUTUBE_API_KEY")

BASE_URL = "https://www.googleapis.com/youtube/v3"

MAX_COMMENTS = 100

ANALYZER_VERSION = "1.0"


# =========================================================
# PATRONES
# =========================================================

# UID de Genshin:
# normalmente 9 o 10 dígitos.
UID_PATTERN = re.compile(
    r"\b(?:uid\s*[:\-]?\s*)?([1-9]\d{8,9})\b",
    re.IGNORECASE
)


SERVER_PATTERN = re.compile(
    r"\b("
    r"america|américa|"
    r"na|"
    r"north america|"
    r"europa|europe|eu|"
    r"asia|"
    r"server|servidor"
    r")\b",
    re.IGNORECASE
)


GIVEAWAY_PATTERN = re.compile(
    r"\b("
    r"welkin|welkins|welking|"
    r"bendi|bendis|"
    r"bendici[oó]n|bendiciones|"
    r"sorteo|sorteando|sortear|"
    r"giveaway|"
    r"premio|ganador|ganadora"
    r")\b",
    re.IGNORECASE
)


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


def get_videos():
    """
    Analizamos los videos almacenados en PostgreSQL.

    Los más recientes primero.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.youtube_video_id,
                    v.title,
                    c.channel_name
                FROM videos v
                JOIN channels c
                    ON c.id = v.channel_id
                ORDER BY v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# YOUTUBE
# =========================================================

def get_comments(video_id):
    """
    Obtiene hasta 100 comentarios principales.

    Usamos la API oficial de YouTube.
    """

    response = requests.get(
        f"{BASE_URL}/commentThreads",
        params={
            "part": "snippet",
            "videoId": video_id,
            "maxResults": MAX_COMMENTS,
            "order": "relevance",
            "textFormat": "plainText",
            "key": API_KEY,
        },
        timeout=20,
    )

    # -----------------------------------------------------
    # Algunos videos pueden tener comentarios desactivados.
    # -----------------------------------------------------

    if response.status_code != 200:

        try:
            error_data = response.json()
        except Exception:
            error_data = {}

        reason = (
            error_data
            .get("error", {})
            .get("errors", [{}])[0]
            .get("reason", "unknown")
        )

        raise RuntimeError(
            f"YouTube API: "
            f"{response.status_code} - "
            f"{reason}"
        )

    data = response.json()

    comments = []

    for item in data.get(
        "items",
        []
    ):

        snippet = (
            item["snippet"]
            ["topLevelComment"]
            ["snippet"]
        )

        comment_id = (
            item["snippet"]
            ["topLevelComment"]
            ["id"]
        )

        text = snippet.get(
            "textDisplay",
            ""
        )

        comments.append({
            "id": comment_id,
            "text": text,
        })

    return comments


# =========================================================
# ANALIZAR COMENTARIOS
# =========================================================

def analyze_comments(comments):

    uid_count = 0
    server_count = 0
    giveaway_count = 0
    combined_count = 0

    evidence = []

    for comment in comments:

        text = comment["text"]

        has_uid = bool(
            UID_PATTERN.search(text)
        )

        has_server = bool(
            SERVER_PATTERN.search(text)
        )

        has_giveaway = bool(
            GIVEAWAY_PATTERN.search(text)
        )

        if has_uid:
            uid_count += 1

        if has_server:
            server_count += 1

        if has_giveaway:
            giveaway_count += 1

        # -------------------------------------------------
        # Señal combinada
        # -------------------------------------------------
        #
        # Especialmente interesante:
        #
        # UID + servidor
        # UID + palabra de giveaway
        # -------------------------------------------------

        combined = (
            has_uid
            and (
                has_server
                or has_giveaway
            )
        )

        if combined:
            combined_count += 1

        # -------------------------------------------------
        # No necesitamos guardar los 100 comentarios.
        #
        # Guardamos únicamente los que contienen
        # alguna evidencia.
        # -------------------------------------------------

        if (
            has_uid
            or has_server
            or has_giveaway
        ):

            evidence.append({
                "comment_id": comment["id"],
                "text": text,
                "has_uid": has_uid,
                "has_server": has_server,
                "has_giveaway": has_giveaway,
            })

    return {
        "total": len(comments),

        "uid_count": uid_count,

        "server_count": server_count,

        "giveaway_count": giveaway_count,

        "combined_count": combined_count,

        "evidence": evidence,
    }


# =========================================================
# CLASIFICACIÓN
# =========================================================

def classify(result):

    total = result["total"]

    uid_count = result["uid_count"]

    giveaway_count = (
        result["giveaway_count"]
    )

    combined_count = (
        result["combined_count"]
    )

    # -----------------------------------------------------
    # Sin comentarios
    # -----------------------------------------------------

    if total == 0:

        return (
            "SIN_DATOS",
            0
        )

    uid_ratio = (
        uid_count / total
    )

    combined_ratio = (
        combined_count / total
    )

    score = 0

    # -----------------------------------------------------
    # Concentración alta de UID
    # -----------------------------------------------------

    if (
        uid_count >= 10
        and uid_ratio >= 0.20
    ):

        score += 4

    elif uid_count >= 3:

        score += 2

    elif uid_count >= 1:

        score += 1

    # -----------------------------------------------------
    # Comentarios hablando explícitamente del giveaway
    # -----------------------------------------------------

    if giveaway_count >= 3:

        score += 3

    elif giveaway_count >= 1:

        score += 1

    # -----------------------------------------------------
    # UID combinado con servidor/giveaway
    # -----------------------------------------------------

    if (
        combined_count >= 5
        or combined_ratio >= 0.15
    ):

        score += 3

    elif combined_count >= 2:

        score += 2

    elif combined_count >= 1:

        score += 1

    # -----------------------------------------------------
    # Clasificación
    # -----------------------------------------------------

    if score >= 7:

        return (
            "FUERTE",
            score
        )

    if score >= 3:

        return (
            "DUDOSO",
            score
        )

    return (
        "DEBIL",
        score
    )


# =========================================================
# PERSISTENCIA
# =========================================================

def save_analysis(
    video_id,
    result,
    classification,
    score
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            # -------------------------------------------------
            # Análisis principal
            # -------------------------------------------------

            cur.execute(
                """
                INSERT INTO comment_analysis (
                    video_id,
                    comments_analyzed,
                    uid_count,
                    server_count,
                    giveaway_count,
                    combined_count,
                    classification,
                    score,
                    analyzer_version,
                    analyzed_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (video_id)
                DO UPDATE SET
                    comments_analyzed =
                        EXCLUDED.comments_analyzed,

                    uid_count =
                        EXCLUDED.uid_count,

                    server_count =
                        EXCLUDED.server_count,

                    giveaway_count =
                        EXCLUDED.giveaway_count,

                    combined_count =
                        EXCLUDED.combined_count,

                    classification =
                        EXCLUDED.classification,

                    score =
                        EXCLUDED.score,

                    analyzer_version =
                        EXCLUDED.analyzer_version,

                    analyzed_at =
                        CURRENT_TIMESTAMP

                RETURNING id
                """,
                (
                    video_id,

                    result["total"],

                    result["uid_count"],

                    result["server_count"],

                    result["giveaway_count"],

                    result["combined_count"],

                    classification,

                    score,

                    ANALYZER_VERSION,
                )
            )

            analysis_id = (
                cur.fetchone()[0]
            )

            # -------------------------------------------------
            # Borrar evidencia anterior
            # -------------------------------------------------

            cur.execute(
                """
                DELETE FROM comment_evidence
                WHERE analysis_id = %s
                """,
                (
                    analysis_id,
                )
            )

            # -------------------------------------------------
            # Guardar evidencia
            # -------------------------------------------------

            for item in result["evidence"]:

                cur.execute(
                    """
                    INSERT INTO comment_evidence (
                        analysis_id,
                        youtube_comment_id,
                        has_uid,
                        has_server,
                        has_giveaway_term,
                        evidence_text
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        analysis_id,

                        item["comment_id"],

                        item["has_uid"],

                        item["has_server"],

                        item["has_giveaway"],

                        item["text"],
                    )
                )

        conn.commit()


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)

    print(
        "ANALIZADOR DE COMENTARIOS"
    )

    print(
        "Versión:",
        ANALYZER_VERSION
    )

    print("=" * 70)

    videos = get_videos()

    print(
        "Videos:",
        len(videos)
    )

    totals = {
        "FUERTE": 0,
        "DUDOSO": 0,
        "DEBIL": 0,
        "SIN_DATOS": 0,
        "ERROR": 0,
    }

    for number, video in enumerate(
        videos,
        start=1
    ):

        (
            video_db_id,
            youtube_video_id,
            title,
            channel,
        ) = video

        print()
        print("-" * 70)

        print(
            f"[{number}/"
            f"{len(videos)}] "
            f"{channel}"
        )

        print(title)

        try:

            comments = get_comments(
                youtube_video_id
            )

        except Exception as e:

            totals["ERROR"] += 1

            print(
                "ERROR:",
                e
            )

            continue

        result = analyze_comments(
            comments
        )

        classification, score = classify(
            result
        )

        totals[classification] += 1

        save_analysis(
            video_db_id,
            result,
            classification,
            score
        )

        print(
            f"{classification} | "
            f"score={score}"
        )

        print(
            "Comentarios:",
            result["total"],
            "| UID:",
            result["uid_count"],
            "| servidor:",
            result["server_count"],
            "| giveaway:",
            result["giveaway_count"],
            "| combinados:",
            result["combined_count"],
        )

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    for key, value in totals.items():

        print(
            f"{key:15} : {value}"
        )


if __name__ == "__main__":
    main()

