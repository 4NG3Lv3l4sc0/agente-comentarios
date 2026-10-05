import os
import re
import json

import psycopg


# =========================================================
# CONFIGURACIÓN
# =========================================================

CLASSIFIER_VERSION = "1.0"


# =========================================================
# METADATOS
# =========================================================

# Los metadatos son una señal auxiliar.
#
# No esperamos que los creadores pongan normalmente
# "sorteo" en el título.

WELKIN_PATTERN = re.compile(
    r"\b("
    r"welkin|welkins|welking|"
    r"bendi|bendis|"
    r"bendici[oó]n(?:es)? lunar(?:es)?"
    r")\b",
    re.IGNORECASE
)


GIVEAWAY_PATTERN = re.compile(
    r"\b("
    r"sorteo|sorteando|sortear|"
    r"giveaway|"
    r"regalando"
    r")\b",
    re.IGNORECASE
)


BIG_PRIZE_PATTERN = re.compile(
    r"(?:"
    r"\$\s*(?:50|100|200|300|500|1000)"
    r"|"
    r"pack(?:s)?\s+de\s+\$?\s*"
    r"(?:50|100|200|300|500|1000)"
    r")",
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
    Obtiene toda la evidencia actualmente disponible.

    LEFT JOIN es importante porque un video puede tener:

    - comentarios pero no transcripción,
    - transcripción pero no comentarios,
    - ambos,
    - ninguno.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.youtube_video_id,
                    v.title,
                    COALESCE(v.description, ''),

                    c.channel_name,

                    vt.status,

                    ta.classification,
                    ta.score,

                    ca.classification,
                    ca.score,
                    ca.comments_analyzed,
                    ca.uid_count,
                    ca.server_count,
                    ca.giveaway_count,
                    ca.combined_count

                FROM videos v

                JOIN channels c
                    ON c.id = v.channel_id

                LEFT JOIN video_transcripts vt
                    ON vt.video_id = v.id

                LEFT JOIN transcript_analysis ta
                    ON ta.video_id = v.id

                LEFT JOIN comment_analysis ca
                    ON ca.video_id = v.id

                ORDER BY v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# METADATOS
# =========================================================

def analyze_metadata(
    title,
    description
):
    """
    Analiza únicamente el título como señal automática.

    La descripción se conserva para análisis posteriores,
    pero no suma puntuación porque puede contener plantillas,
    promociones o información repetitiva del canal.
    """

    text = title

    score = 0
    reasons = []

    # -----------------------------------------------------
    # Welkin / Bendición
    # -----------------------------------------------------

    if WELKIN_PATTERN.search(text):

        score += 3

        reasons.append(
            "Título menciona Welkin/Bendición"
        )

    # -----------------------------------------------------
    # Sorteo explícito
    # -----------------------------------------------------

    if GIVEAWAY_PATTERN.search(text):

        score += 2

        reasons.append(
            "Título menciona sorteo"
        )

    # -----------------------------------------------------
    # IMPORTANTE:
    #
    # Una cantidad monetaria NO constituye por sí sola
    # evidencia de giveaway.
    #
    # Ejemplo:
    # "Gastó $3000 en Odette"
    #
    # no debe sumar puntos.
    # -----------------------------------------------------

    return score, reasons

# =========================================================
# CLASIFICACIÓN GLOBAL
# =========================================================

def classify_video(
    transcript_status,
    transcript_classification,
    transcript_score,
    comment_classification,
    comment_score,
    comments_analyzed,
    uid_count,
    giveaway_count,
    combined_count,
    metadata_score,
    metadata_reasons
):

    score = 0

    reasons = list(
        metadata_reasons
    )

    # =====================================================
    # TRANSCRIPCIÓN
    # =====================================================

    transcript_available = (
        transcript_classification
        is not None
    )

    if transcript_classification == "POSIBLE_SORTEO":

        score += 6

        reasons.append(
            "Transcripción con señales fuertes de sorteo"
        )

    elif transcript_classification == "DUDOSO":

        score += 4

        reasons.append(
            "Transcripción con señales que requieren revisión"
        )

    elif transcript_classification == "SIN_SENALES":

        # No restamos puntos.
        #
        # Un creador puede usar jerga que todavía
        # no reconoce nuestro detector.

        reasons.append(
            "Transcripción analizada sin señales reconocidas"
        )

    # =====================================================
    # COMENTARIOS
    # =====================================================

    if comment_classification == "FUERTE":

        score += 5

        reasons.append(
            "Alta concentración de señales en comentarios"
        )

    elif comment_classification == "DUDOSO":

        score += 3

        reasons.append(
            "Comentarios contienen señales relevantes"
        )

    elif comment_classification == "DEBIL":

        score += 1

    # -----------------------------------------------------
    # UID
    # -----------------------------------------------------

    if comments_analyzed:

        uid_ratio = (
            uid_count
            / comments_analyzed
        )

    else:

        uid_ratio = 0

    if (
        uid_count >= 10
        and uid_ratio >= 0.20
    ):

        score += 3

        reasons.append(
            f"Alta concentración de UID "
            f"({uid_count}/{comments_analyzed})"
        )

    elif uid_count >= 3:

        score += 1

        reasons.append(
            f"Varios comentarios con UID "
            f"({uid_count})"
        )

    # -----------------------------------------------------
    # Combinados
    # -----------------------------------------------------

    if combined_count >= 5:

        score += 2

        reasons.append(
            f"{combined_count} comentarios combinan "
            f"UID con servidor/términos de giveaway"
        )

    elif combined_count >= 2:

        score += 1

        reasons.append(
            f"{combined_count} comentarios tienen "
            f"señales combinadas"
        )

    # -----------------------------------------------------
    # Giveaway explícito en comentarios
    # -----------------------------------------------------

    if giveaway_count >= 3:

        score += 1

        reasons.append(
            f"{giveaway_count} comentarios mencionan "
            f"términos relacionados con giveaway"
        )

    # =====================================================
    # METADATOS
    # =====================================================

    score += metadata_score

    # =====================================================
    # DISPONIBILIDAD DE INFORMACIÓN
    # =====================================================

    comments_available = (
        comment_classification
        is not None
    )

    # -----------------------------------------------------
    # Caso sin fuentes suficientes
    # -----------------------------------------------------

    if (
        not transcript_available
        and not comments_available
    ):

        return (
            "PENDIENTE",
            score,
            reasons
        )

    # -----------------------------------------------------
    # Evitar falsos NORMAL
    # -----------------------------------------------------
    #
    # Si todavía no tenemos transcripción, una puntuación
    # baja NO significa que podamos afirmar NORMAL.
    # -----------------------------------------------------

    if not transcript_available:

        if score >= 8:

            return (
                "CANDIDATO_FUERTE",
                score,
                reasons
            )

        if score >= 3:

            return (
                "DUDOSO",
                score,
                reasons
            )

        reasons.append(
            "Falta análisis de transcripción"
        )

        return (
            "PENDIENTE",
            score,
            reasons
        )

    # =====================================================
    # CON TRANSCRIPCIÓN DISPONIBLE
    # =====================================================

    if score >= 10:

        return (
            "CANDIDATO_FUERTE",
            score,
            reasons
        )

    if score >= 5:

        return (
            "DUDOSO",
            score,
            reasons
        )

    return (
        "SIN_SENALES",
        score,
        reasons
    )


# =========================================================
# PERSISTENCIA
# =========================================================

def save_result(
    video_id,
    classification,
    global_score,
    transcript_score,
    comment_score,
    metadata_score,
    transcript_status,
    transcript_classification,
    comment_classification,
    reasons
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO global_analysis (
                    video_id,
                    classification,
                    global_score,
                    transcript_score,
                    comment_score,
                    metadata_score,
                    transcript_status,
                    transcript_classification,
                    comment_classification,
                    reasons,
                    classifier_version,
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
                    %s::jsonb,
                    %s,
                    CURRENT_TIMESTAMP
                )

                ON CONFLICT (video_id)

                DO UPDATE SET
                    classification =
                        EXCLUDED.classification,

                    global_score =
                        EXCLUDED.global_score,

                    transcript_score =
                        EXCLUDED.transcript_score,

                    comment_score =
                        EXCLUDED.comment_score,

                    metadata_score =
                        EXCLUDED.metadata_score,

                    transcript_status =
                        EXCLUDED.transcript_status,

                    transcript_classification =
                        EXCLUDED.transcript_classification,

                    comment_classification =
                        EXCLUDED.comment_classification,

                    reasons =
                        EXCLUDED.reasons,

                    classifier_version =
                        EXCLUDED.classifier_version,

                    analyzed_at =
                        CURRENT_TIMESTAMP
                """,
                (
                    video_id,
                    classification,
                    global_score,
                    transcript_score or 0,
                    comment_score or 0,
                    metadata_score,
                    transcript_status,
                    transcript_classification,
                    comment_classification,

                    json.dumps(
                        reasons,
                        ensure_ascii=False
                    ),

                    CLASSIFIER_VERSION,
                )
            )

        conn.commit()


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("CLASIFICADOR GLOBAL")
    print(
        "Versión:",
        CLASSIFIER_VERSION
    )
    print("=" * 70)

    videos = get_videos()

    totals = {
        "CANDIDATO_FUERTE": 0,
        "DUDOSO": 0,
        "SIN_SENALES": 0,
        "PENDIENTE": 0,
    }

    for number, row in enumerate(
        videos,
        start=1
    ):

        (
            video_id,
            youtube_video_id,
            title,
            description,
            channel,

            transcript_status,

            transcript_classification,
            transcript_score,

            comment_classification,
            comment_score,
            comments_analyzed,
            uid_count,
            server_count,
            giveaway_count,
            combined_count,

        ) = row

        # Valores NULL -> 0

        transcript_score = (
            transcript_score or 0
        )

        comment_score = (
            comment_score or 0
        )

        comments_analyzed = (
            comments_analyzed or 0
        )

        uid_count = (
            uid_count or 0
        )

        giveaway_count = (
            giveaway_count or 0
        )

        combined_count = (
            combined_count or 0
        )

        # -------------------------------------------------
        # Metadatos
        # -------------------------------------------------

        (
            metadata_score,
            metadata_reasons
        ) = analyze_metadata(
            title,
            description
        )

        # -------------------------------------------------
        # Clasificación
        # -------------------------------------------------

        (
            classification,
            global_score,
            reasons
        ) = classify_video(
            transcript_status,
            transcript_classification,
            transcript_score,
            comment_classification,
            comment_score,
            comments_analyzed,
            uid_count,
            giveaway_count,
            combined_count,
            metadata_score,
            metadata_reasons
        )

        totals[classification] += 1

        # -------------------------------------------------
        # Persistencia
        # -------------------------------------------------

        save_result(
            video_id,
            classification,
            global_score,
            transcript_score,
            comment_score,
            metadata_score,
            transcript_status,
            transcript_classification,
            comment_classification,
            reasons
        )

        # -------------------------------------------------
        # Terminal
        # -------------------------------------------------

        print()
        print(
            f"[{number}/{len(videos)}] "
            f"{channel}"
        )

        print(title)

        print(
            f"{classification} | "
            f"global={global_score} | "
            f"transcript={transcript_score} | "
            f"comments={comment_score} | "
            f"metadata={metadata_score}"
        )

        if classification in (
            "CANDIDATO_FUERTE",
            "DUDOSO"
        ):

            for reason in reasons:

                print(
                    "  -",
                    reason
                )

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    for key, value in totals.items():

        print(
            f"{key:20}: {value}"
        )


if __name__ == "__main__":
    main()

