import os
import json
from datetime import datetime, timezone

import psycopg
from youtube_transcript_api import YouTubeTranscriptApi


# =========================================================
# CONFIGURACIÓN
# =========================================================

LANGUAGES = [
    "es",
    "es-419",
    "en",
]

# Número máximo de videos nuevos que intentaremos consultar
# en una sola ejecución.
MAX_VIDEOS_PER_RUN = 5


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


def get_pending_videos(limit=MAX_VIDEOS_PER_RUN):
    """
    Prioriza videos que el clasificador global considera
    candidatos o dudosos.

    Orden:
    1. CANDIDATO_FUERTE
    2. DUDOSO
    3. PENDIENTE
    4. SIN_SENALES

    Solo selecciona videos que todavía no tengan una fila
    en video_transcripts.
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

                LEFT JOIN video_transcripts vt
                    ON vt.video_id = v.id

                LEFT JOIN global_analysis ga
                    ON ga.video_id = v.id

                WHERE vt.id IS NULL

                ORDER BY

                    CASE ga.classification

                        WHEN 'CANDIDATO_FUERTE'
                            THEN 1

                        WHEN 'DUDOSO'
                            THEN 2

                        WHEN 'PENDIENTE'
                            THEN 3

                        WHEN 'SIN_SENALES'
                            THEN 4

                        ELSE 5

                    END,

                    ga.global_score DESC NULLS LAST,

                    v.published_at DESC

                LIMIT %s
                """,
                (
                    limit,
                )
            )

            return cur.fetchall()
# =========================================================
# GUARDAR RESULTADOS
# =========================================================

def save_success(
    video_db_id,
    language,
    transcript_text,
    transcript_json
):
    """
    Guarda permanentemente una transcripción obtenida.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO video_transcripts (
                    video_id,
                    language,
                    transcript_text,
                    transcript_json,
                    status,
                    error_type,
                    fetched_at,
                    last_attempt_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    'OBTENIDA',
                    NULL,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (video_id)
                DO UPDATE SET
                    language = EXCLUDED.language,
                    transcript_text = EXCLUDED.transcript_text,
                    transcript_json = EXCLUDED.transcript_json,
                    status = 'OBTENIDA',
                    error_type = NULL,
                    fetched_at = CURRENT_TIMESTAMP,
                    last_attempt_at = CURRENT_TIMESTAMP
                """,
                (
                    video_db_id,
                    language,
                    transcript_text,
                    json.dumps(
                        transcript_json,
                        ensure_ascii=False
                    ),
                )
            )

        conn.commit()


def save_status(
    video_db_id,
    status,
    error_type
):
    """
    Guarda un estado cuando no pudimos obtener
    la transcripción.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO video_transcripts (
                    video_id,
                    status,
                    error_type,
                    last_attempt_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (video_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    error_type = EXCLUDED.error_type,
                    last_attempt_at = CURRENT_TIMESTAMP
                """,
                (
                    video_db_id,
                    status,
                    error_type,
                )
            )

        conn.commit()


# =========================================================
# CONVERSIÓN DE TRANSCRIPCIÓN
# =========================================================

def transcript_to_data(transcript):
    """
    Convierte los objetos devueltos por
    youtube-transcript-api a estructuras simples
    que podemos guardar como JSON.
    """

    segments = []

    texts = []

    for segment in transcript:

        item = {
            "text": segment.text,
            "start": float(segment.start),
            "duration": float(segment.duration),
        }

        segments.append(item)

        texts.append(segment.text)

    full_text = " ".join(texts)

    return full_text, segments


# =========================================================
# CLASIFICACIÓN DE ERRORES
# =========================================================

def classify_error(exception):
    """
    Diferencia bloqueo de IP de otros errores.

    No queremos confundir:
        IP bloqueada
    con:
        video sin subtítulos.
    """

    error_name = type(exception).__name__

    error_text = str(exception).lower()

    if (
        "ipblocked" in error_name.lower()
        or "ip blocked" in error_text
        or "blocking requests from your ip" in error_text
    ):
        return "IP_BLOCKED"

    if (
        "transcriptsdisabled" in error_name.lower()
        or "notranscriptfound" in error_name.lower()
        or "transcript not available" in error_text
    ):
        return "NO_DISPONIBLE"

    return "ERROR"


# =========================================================
# PROGRAMA PRINCIPAL
# =========================================================

def main():

    print("=" * 70)
    print("DESCARGADOR DE TRANSCRIPCIONES")
    print("=" * 70)

    videos = get_pending_videos()

    print(
        f"Videos pendientes seleccionados: {len(videos)}"
    )

    if not videos:

        print(
            "No hay videos nuevos que consultar."
        )

        return

    api = YouTubeTranscriptApi()

    obtained = 0
    unavailable = 0
    errors = 0

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
            f"[{number}/{len(videos)}] "
            f"{channel}"
        )

        print(title)

        print(
            "YouTube ID:",
            youtube_video_id
        )

        try:

            transcript = api.fetch(
                youtube_video_id,
                languages=LANGUAGES
            )

            full_text, segments = transcript_to_data(
                transcript
            )

            language = getattr(
                transcript,
                "language_code",
                None
            )

            save_success(
                video_db_id,
                language,
                full_text,
                segments
            )

            obtained += 1

            print(
                "OBTENIDA |",
                f"{len(segments)} segmentos"
            )

        except Exception as e:

            status = classify_error(e)

            error_name = type(e).__name__

            # ---------------------------------------------
            # IP bloqueada
            # ---------------------------------------------

            if status == "IP_BLOCKED":

                print(
                    "IP_BLOCKED |",
                    error_name
                )

                print()
                print(
                    "YouTube está bloqueando actualmente "
                    "las solicitudes de transcripción."
                )

                print(
                    "Se detiene la ejecución para evitar "
                    "seguir haciendo solicitudes."
                )

                # IMPORTANTE:
                #
                # NO guardamos este video como consultado
                # permanentemente.
                #
                # Así podrá volver a intentarse cuando
                # desaparezca el bloqueo.

                break

            # ---------------------------------------------
            # Sin transcripción
            # ---------------------------------------------

            if status == "NO_DISPONIBLE":

                save_status(
                    video_db_id,
                    "NO_DISPONIBLE",
                    error_name
                )

                unavailable += 1

                print(
                    "NO_DISPONIBLE |",
                    error_name
                )

                continue

            # ---------------------------------------------
            # Otro error
            # ---------------------------------------------

            save_status(
                video_db_id,
                "ERROR",
                error_name
            )

            errors += 1

            print(
                "ERROR |",
                error_name
            )

    print()
    print("=" * 70)
    print("RESUMEN DE ESTA EJECUCIÓN")
    print("=" * 70)

    print(
        "Obtenidas       :",
        obtained
    )

    print(
        "No disponibles  :",
        unavailable
    )

    print(
        "Otros errores   :",
        errors
    )


if __name__ == "__main__":
    main()

