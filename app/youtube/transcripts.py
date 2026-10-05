import os
import re

import psycopg
from youtube_transcript_api import YouTubeTranscriptApi


# =========================================================
# CONFIGURACIÓN
# =========================================================

KEYWORDS = [
    "welkin",
    "bendición",
    "bendicion",
    "bendi",
    "bendis",
    "sorteo",
    "sorteando",
    "sorteamos",
    "sortear",
    "giveaway",
    "uid",
    "servidor",
    "server",
    "participar",
    "participación",
    "participacion",
    "ganador",
    "ganadores",
    "regalando",
    "regalamos",
    "regalo",
    "premio",
    "premios",
]

LANGUAGES = [
    "es",
    "es-419",
    "en",
]


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
# OBTENER VIDEOS PENDIENTES
# =========================================================

def get_pending_videos():

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

                LEFT JOIN video_transcripts vt
                    ON vt.video_id = v.id

                WHERE
                    vt.video_id IS NULL

                ORDER BY
                    v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# FORMATEAR TIEMPO
# =========================================================

def format_time(seconds):

    seconds = int(seconds)

    minutes = seconds // 60
    seconds = seconds % 60

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


# =========================================================
# NORMALIZAR TRANSCRIPCIÓN
# =========================================================

def transcript_to_text(transcript):

    return " ".join(
        segment.text.strip()
        for segment in transcript
        if segment.text.strip()
    )


# =========================================================
# BUSCAR EVIDENCIA
# =========================================================

def find_evidence(transcript):

    segments = list(transcript)

    evidence = []

    used_ranges = []

    for i, segment in enumerate(
        segments
    ):

        text_lower = (
            segment.text.lower()
        )

        found = [
            keyword
            for keyword in KEYWORDS
            if keyword in text_lower
        ]

        if not found:
            continue

        # ---------------------------------------------
        # Contexto alrededor de la palabra encontrada
        # ---------------------------------------------

        start = max(
            0,
            i - 3
        )

        end = min(
            len(segments),
            i + 4
        )

        # ---------------------------------------------
        # Evitar fragmentos prácticamente duplicados
        # ---------------------------------------------

        duplicate = False

        for old_start, old_end in used_ranges:

            if (
                start <= old_end
                and end >= old_start
            ):

                duplicate = True
                break

        if duplicate:
            continue

        used_ranges.append(
            (
                start,
                end,
            )
        )

        context = " ".join(
            segments[j].text
            for j in range(
                start,
                end
            )
        )

        evidence.append(
            {
                "time": format_time(
                    segment.start
                ),
                "keywords": found,
                "context": context,
            }
        )

    return evidence


# =========================================================
# GUARDAR TRANSCRIPCIÓN
# =========================================================

def save_transcript(
    video_db_id,
    transcript,
):

    full_text = transcript_to_text(
        transcript
    )

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO video_transcripts
                (
                    video_id,
                    language,
                    transcript_text,
                    status,
                    error_type,
                    fetched_at,
                    last_attempt_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    'OK',
                    NULL,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (video_id)
                DO UPDATE SET
                    language = EXCLUDED.language,
                    transcript_text = EXCLUDED.transcript_text,
                    status = 'OK',
                    error_type = NULL,
                    fetched_at = CURRENT_TIMESTAMP,
                    last_attempt_at = CURRENT_TIMESTAMP
                """,
                (
                    video_db_id,
                    "auto",
                    full_text,
                )
            )

        conn.commit()

    return full_text

# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("OBTENCION DE TRANSCRIPCIONES")
    print("=" * 70)

    videos = get_pending_videos()

    print(
        "Videos pendientes:",
        len(videos)
    )

    if not videos:

        print(
            "No hay transcripciones pendientes."
        )

        return

    api = YouTubeTranscriptApi()

    processed = 0
    with_evidence = 0
    without_evidence = 0
    unavailable = 0
    errors = 0

    total = len(videos)

    for number, row in enumerate(
        videos,
        start=1
    ):

        (
            video_db_id,
            youtube_video_id,
            title,
            channel_name,
        ) = row

        print()
        print("=" * 70)

        print(
            f"VIDEO {number}/{total}"
        )

        print(
            "Canal:",
            channel_name
        )

        print(
            "Titulo:",
            title
        )

        print(
            "YouTube ID:",
            youtube_video_id
        )

        # =================================================
        # DESCARGAR TRANSCRIPCIÓN
        # =================================================

        try:

            transcript = api.fetch(
                youtube_video_id,
                languages=LANGUAGES,
            )

        except Exception as exc:

            error_name = (
                type(exc).__name__
            )

            print(
                "Sin transcripcion:",
                error_name
            )

            unavailable += 1

            continue

        # =================================================
        # GUARDAR TRANSCRIPCIÓN COMPLETA
        # =================================================

        try:

            full_text = save_transcript(
                video_db_id,
                transcript,
            )

        except Exception as exc:

            print(
                "ERROR guardando transcripcion:",
                type(exc).__name__,
                str(exc),
            )

            errors += 1

            continue

        processed += 1

        print(
            "Caracteres:",
            len(full_text)
        )

        # =================================================
        # BUSCAR EVIDENCIA
        # =================================================

        evidence = find_evidence(
            transcript
        )

        print(
            "Segmentos relevantes:",
            len(evidence)
        )

        if not evidence:

            without_evidence += 1

            print(
                "Sin señales relevantes."
            )

            continue

        with_evidence += 1

        # =================================================
        # MOSTRAR EVIDENCIA
        # =================================================

        for item in evidence:

            print()

            print(
                f"[{item['time']}] "
                f"Palabras: "
                f"{', '.join(item['keywords'])}"
            )

            print(
                "Contexto:"
            )

            print(
                item["context"]
            )

        print()

        print(
            ">>> VIDEO CON EVIDENCIA "
            "EN TRANSCRIPCION <<<"
        )

        # MUY IMPORTANTE:
        # NO hay return aquí.
        #
        # El ciclo continúa con el siguiente video.

    # =====================================================
    # RESUMEN
    # =====================================================

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    print(
        "Videos pendientes:",
        total
    )

    print(
        "Transcripciones guardadas:",
        processed
    )

    print(
        "Con evidencia:",
        with_evidence
    )

    print(
        "Sin evidencia:",
        without_evidence
    )

    print(
        "Sin transcripcion:",
        unavailable
    )

    print(
        "Errores:",
        errors
    )


if __name__ == "__main__":
    main()
