import os
import re
import json

import psycopg


# =========================================================
# CONFIGURACIÓN
# =========================================================

CONTEXT_BEFORE = 20
CONTEXT_AFTER = 20

DETECTOR_VERSION = "1.0"


# =========================================================
# PATRONES
# =========================================================

STRONG_PATTERNS = [
    # Welkin
    r"\bwelkin(?:s|g)?\b",

    # Bendición formal
    r"\bbendici[oó]n(?:es)? lunar(?:es)?\b",
    r"\bbendici[oó]n(?:es)? de la luna\b",

    # Jerga: bendi / bendis
    r"\bbendi(?:s)?\b",

    # Sorteos
    r"\bsorteo(?:s)?\b",
    r"\bsorteando\b",
    r"\bsortear\b",
    r"\bsortear[eé]\b",
    r"\bsorteamos\b",

    # Inglés
    r"\bgiveaway(?:s)?\b",
]


PARTICIPATION_PATTERNS = [
    r"\bparticipar\b",
    r"\bparticipaci[oó]n\b",
    r"\bparticipa\b",
    r"\bparticipen\b",

    r"\bganador\b",
    r"\bganadora\b",
    r"\bganadores\b",

    r"\bganar\b",
    r"\bpremio\b",

    r"\bregalando\b",
    r"\bregalar\b",

    r"\bdeja(?:r)? un comentario\b",
    r"\bdejad un comentario\b",
    r"\bdejen un comentario\b",
]


IDENTIFICATION_PATTERNS = [
    r"\buid\b",

    r"\bservidor\b",
    r"\bserver\b",

    r"\bam[eé]rica\b",
    r"\bamerica\b",
    r"\beuropa\b",
    r"\basia\b",
]


PRIZE_PATTERNS = [
    # Packs monetarios
    r"\bpack(?:s)? de \$?\s*100\b",
    r"\bpack(?:s)? de \$?\s*\d+\b",

    # Ejemplo: "2 bendis"
    r"\b\d+\s+bendi(?:s)?\b",

    # Welkin
    r"\bwelkin(?:s|g)?\b",

    # Bendición formal
    r"\bbendici[oó]n(?:es)? lunar(?:es)?\b",
    r"\bbendici[oó]n(?:es)? de la luna\b",
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


def get_transcripts():
    """
    Obtiene únicamente transcripciones que ya fueron
    almacenadas correctamente en PostgreSQL.

    Este detector NO consulta YouTube.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.youtube_video_id,
                    v.title,
                    c.channel_name,
                    c.youtube_handle,
                    vt.language,
                    vt.transcript_json
                FROM video_transcripts vt
                JOIN videos v
                    ON v.id = vt.video_id
                JOIN channels c
                    ON c.id = v.channel_id
                WHERE vt.status = 'OBTENIDA'
                  AND vt.transcript_json IS NOT NULL
                ORDER BY v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# UTILIDADES
# =========================================================

def find_patterns(text, patterns):
    found = []

    for pattern in patterns:
        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):
            found.append(pattern)

    return found


def clean_pattern(pattern):
    pattern = pattern.replace(
        r"\b",
        ""
    )

    pattern = pattern.replace(
        "\\",
        ""
    )

    return pattern


def format_time(seconds):
    seconds = max(
        0,
        int(seconds)
    )

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    seconds = seconds % 60

    if hours:
        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


# =========================================================
# NORMALIZAR TRANSCRIPCIÓN
# =========================================================

def normalize_segments(data):
    """
    Convierte transcript_json en una lista uniforme de:

    {
        text,
        start,
        duration,
        end
    }
    """

    if isinstance(
        data,
        str
    ):
        data = json.loads(data)

    segments = []

    for item in data:

        text = str(
            item.get(
                "text",
                ""
            )
        ).strip()

        if not text:
            continue

        start = float(
            item.get(
                "start",
                0
            )
        )

        duration = float(
            item.get(
                "duration",
                0
            )
        )

        segments.append({
            "text": text,
            "start": start,
            "duration": duration,
            "end": start + duration,
        })

    return segments


# =========================================================
# BUSCAR SEGMENTOS CON SEÑALES
# =========================================================

def find_signal_segments(segments):
    """
    Busca segmentos que contengan señales potencialmente
    relacionadas con giveaways.

    UID/servidor por sí solos NO generan una señal.
    """

    signals = []

    for segment in segments:

        text = segment["text"]

        strong = find_patterns(
            text,
            STRONG_PATTERNS
        )

        participation = find_patterns(
            text,
            PARTICIPATION_PATTERNS
        )

        identification = find_patterns(
            text,
            IDENTIFICATION_PATTERNS
        )

        prize = find_patterns(
            text,
            PRIZE_PATTERNS
        )

        # UID solo no basta.
        if (
            not strong
            and not participation
            and not prize
        ):
            continue

        signals.append({
            "start": segment["start"],
            "end": segment["end"],
            "strong": strong,
            "participation": participation,
            "identification": identification,
            "prize": prize,
        })

    return signals


# =========================================================
# CREAR Y FUSIONAR VENTANAS
# =========================================================

def build_windows(signals):
    """
    Cada señal genera una ventana temporal:

        -20 segundos
        señal
        +20 segundos

    Si varias ventanas se solapan, se fusionan.
    """

    if not signals:
        return []

    raw_windows = []

    for signal in signals:

        start = max(
            0,
            signal["start"] - CONTEXT_BEFORE
        )

        end = (
            signal["end"]
            + CONTEXT_AFTER
        )

        raw_windows.append({
            "start": start,
            "end": end,
        })

    raw_windows.sort(
        key=lambda x: x["start"]
    )

    merged = [
        raw_windows[0]
    ]

    for window in raw_windows[1:]:

        last = merged[-1]

        if (
            window["start"]
            <= last["end"]
        ):

            last["end"] = max(
                last["end"],
                window["end"]
            )

        else:

            merged.append(
                window
            )

    return merged


# =========================================================
# EXTRAER TEXTO DE UNA VENTANA
# =========================================================

def get_window_text(
    segments,
    start,
    end
):
    texts = []

    for segment in segments:

        if (
            segment["end"] >= start
            and segment["start"] <= end
        ):

            texts.append(
                segment["text"]
            )

    return " ".join(texts)


# =========================================================
# ANALIZAR VENTANA
# =========================================================

def analyze_window(text):
    strong = find_patterns(
        text,
        STRONG_PATTERNS
    )

    participation = find_patterns(
        text,
        PARTICIPATION_PATTERNS
    )

    identification = find_patterns(
        text,
        IDENTIFICATION_PATTERNS
    )

    prize = find_patterns(
        text,
        PRIZE_PATTERNS
    )

    score = 0

    # Sorteo / Welkin / Bendi
    if strong:
        score += 3

    # Premio concreto
    if prize:
        score += 2

    # Lenguaje de participación
    if participation:
        score += 2

    # UID / servidor únicamente refuerza
    if identification:
        score += 1

    return {
        "score": score,
        "strong": strong,
        "prize": prize,
        "participation": participation,
        "identification": identification,
    }


# =========================================================
# ANALIZAR VIDEO
# =========================================================

def analyze_video(transcript_json):
    segments = normalize_segments(
        transcript_json
    )

    signals = find_signal_segments(
        segments
    )

    windows = build_windows(
        signals
    )

    evidence = []

    for window in windows:

        text = get_window_text(
            segments,
            window["start"],
            window["end"]
        )

        analysis = analyze_window(
            text
        )

        evidence.append({
            "start": window["start"],
            "end": window["end"],
            "text": text,
            **analysis,
        })

    return evidence


# =========================================================
# CLASIFICACIÓN
# =========================================================

def classify(evidence):
    if not evidence:
        return (
            "SIN_SENALES",
            0
        )

    max_score = max(
        item["score"]
        for item in evidence
    )

    if max_score >= 7:
        return (
            "POSIBLE_SORTEO",
            max_score
        )

    if max_score >= 5:
        return (
            "DUDOSO",
            max_score
        )

    return (
        "SIN_SENALES",
        max_score
    )


# =========================================================
# PERSISTENCIA
# =========================================================

def save_analysis(
    video_id,
    classification,
    score,
    evidence
):
    """
    Guarda o actualiza el análisis del video.

    Cuando volvamos a ejecutar una nueva versión del
    detector:

    1. Actualiza transcript_analysis.
    2. Elimina las evidencias anteriores.
    3. Inserta las evidencias recién calculadas.

    No duplica el análisis del mismo video.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            # -------------------------------------------------
            # ANÁLISIS PRINCIPAL
            # -------------------------------------------------

            cur.execute(
                """
                INSERT INTO transcript_analysis (
                    video_id,
                    classification,
                    score,
                    evidence_count,
                    detector_version,
                    analyzed_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (video_id)
                DO UPDATE SET
                    classification =
                        EXCLUDED.classification,
                    score =
                        EXCLUDED.score,
                    evidence_count =
                        EXCLUDED.evidence_count,
                    detector_version =
                        EXCLUDED.detector_version,
                    analyzed_at =
                        CURRENT_TIMESTAMP
                RETURNING id
                """,
                (
                    video_id,
                    classification,
                    score,
                    len(evidence),
                    DETECTOR_VERSION,
                )
            )

            analysis_id = cur.fetchone()[0]

            # -------------------------------------------------
            # BORRAR EVIDENCIA ANTERIOR
            # -------------------------------------------------

            cur.execute(
                """
                DELETE FROM transcript_evidence
                WHERE analysis_id = %s
                """,
                (
                    analysis_id,
                )
            )

            # -------------------------------------------------
            # INSERTAR NUEVA EVIDENCIA
            # -------------------------------------------------

            for item in evidence:

                strong_json = json.dumps(
                    item["strong"],
                    ensure_ascii=False
                )

                prize_json = json.dumps(
                    item["prize"],
                    ensure_ascii=False
                )

                participation_json = json.dumps(
                    item["participation"],
                    ensure_ascii=False
                )

                identification_json = json.dumps(
                    item["identification"],
                    ensure_ascii=False
                )

                cur.execute(
                    """
                    INSERT INTO transcript_evidence (
                        analysis_id,
                        start_seconds,
                        end_seconds,
                        score,
                        strong_signals,
                        prize_signals,
                        participation_signals,
                        identification_signals,
                        context
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s
                    )
                    """,
                    (
                        analysis_id,
                        item["start"],
                        item["end"],
                        item["score"],
                        strong_json,
                        prize_json,
                        participation_json,
                        identification_json,
                        item["text"],
                    )
                )

        conn.commit()


# =========================================================
# MOSTRAR EVIDENCIA
# =========================================================

def print_evidence(evidence):
    evidence = sorted(
        evidence,
        key=lambda x: x["score"],
        reverse=True
    )

    # Máximo tres ventanas en terminal.
    for item in evidence[:3]:

        print()

        print(
            "  VENTANA:",
            f"{format_time(item['start'])}"
            " - "
            f"{format_time(item['end'])}"
        )

        print(
            "  SCORE:",
            item["score"]
        )

        if item["strong"]:

            print(
                "  FUERTE:",
                ", ".join(
                    clean_pattern(x)
                    for x in item["strong"]
                )
            )

        if item["prize"]:

            print(
                "  PREMIO:",
                ", ".join(
                    clean_pattern(x)
                    for x in item["prize"]
                )
            )

        if item["participation"]:

            print(
                "  PARTICIPACION:",
                ", ".join(
                    clean_pattern(x)
                    for x in item["participation"]
                )
            )

        if item["identification"]:

            print(
                "  IDENTIFICACION:",
                ", ".join(
                    clean_pattern(x)
                    for x in item["identification"]
                )
            )

        print(
            "  CONTEXTO:"
        )

        print(
            "  ",
            item["text"][:1500]
        )


# =========================================================
# PROGRAMA PRINCIPAL
# =========================================================

def main():
    print("=" * 70)
    print("DETECTOR LOCAL DE GIVEAWAYS")
    print("Fuente: PostgreSQL")
    print(
        "Versión del detector:",
        DETECTOR_VERSION
    )
    print("=" * 70)

    transcripts = get_transcripts()

    print(
        "Transcripciones almacenadas:",
        len(transcripts)
    )

    totals = {
        "POSIBLE_SORTEO": 0,
        "DUDOSO": 0,
        "SIN_SENALES": 0,
    }

    # -----------------------------------------------------
    # Procesar cada transcripción almacenada
    # -----------------------------------------------------

    for number, row in enumerate(
        transcripts,
        start=1
    ):

        (
            video_db_id,
            youtube_video_id,
            title,
            channel,
            handle,
            language,
            transcript_json,
        ) = row

        print()
        print("-" * 70)

        print(
            f"[{number}/"
            f"{len(transcripts)}] "
            f"{channel}"
        )

        print(title)

        print(
            "YouTube ID:",
            youtube_video_id
        )

        print(
            "Idioma:",
            language
        )

        # -------------------------------------------------
        # DETECCIÓN LOCAL
        # -------------------------------------------------

        evidence = analyze_video(
            transcript_json
        )

        classification, score = classify(
            evidence
        )

        totals[classification] += 1

        # -------------------------------------------------
        # GUARDAR EN POSTGRESQL
        # -------------------------------------------------

        save_analysis(
            video_db_id,
            classification,
            score,
            evidence
        )

        # -------------------------------------------------
        # MOSTRAR RESULTADO
        # -------------------------------------------------

        print(
            f"{classification} | "
            f"score={score} | "
            f"ventanas={len(evidence)}"
        )

        if classification in (
            "POSIBLE_SORTEO",
            "DUDOSO",
        ):

            print_evidence(
                evidence
            )

    # =====================================================
    # RESUMEN
    # =====================================================

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    for key, value in totals.items():

        print(
            f"{key:20} : {value}"
        )

    print()
    print(
        "Resultados guardados en PostgreSQL."
    )


if __name__ == "__main__":
    main()
