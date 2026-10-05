import os
import re
import json
import psycopg


# =========================================================
# CONFIGURACIÓN
# =========================================================

DETECTOR_VERSION = "1.0"

CONTEXT_BEFORE = 250
CONTEXT_AFTER = 500


# =========================================================
# PATRONES
# =========================================================

# Términos que identifican el premio.
WELKIN_PATTERNS = [
    r"\bwelkin\b",
    r"\bwelkin moon\b",
    r"\bbendici[oó]n lunar\b",
    r"\bbendici[oó]n de la luna\b",
    r"\bbendiciones lunares\b",
    r"\bbendiciones de la luna\b",
    r"\bbendi\b",
    r"\bbendis\b",
]


# Señales fuertes de que se está SORTEANDO la Welkin.
GIVEAWAY_PATTERNS = [
    r"\bsorteo\b",
    r"\bsorteamos\b",
    r"\bsorteando\b",
    r"\bsortear\b",
    r"\bsortear[eé]\b",
    r"\bsortearemos\b",
    r"\bganador\b",
    r"\bganadora\b",
    r"\bganadores\b",
    r"\bparticipar\b",
    r"\bparticipaci[oó]n\b",
]


# Señales típicas de participación.
ENTRY_PATTERNS = [
    r"\buid\b",
    r"\bID\b",
    r"\bservidor\b",
    r"\bserver\b",
    r"\bam[eé]rica\b",
    r"\beuropa\b",
    r"\basia\b",
    r"\bcomentario\b",
    r"\bcomenta\b",
    r"\bcomentar\b",
    r"\bsuscrito\b",
    r"\bsuscrita\b",
    r"\bsuscrib",
]
# Señales características de las Welkin habituales
# ofrecidas a la audiencia del canal.
AUDIENCE_WELKIN_PATTERNS = [
    r"\bten[eé]is\b",
    r"\btienen\b",
    r"\bgratis\b",
    r"\bpor apoyar\b",
    r"\bapoyo al canal\b",
    r"\bapoyar al canal\b",
    r"\bapoyar el canal\b",
    r"\bcanal\b",
    r"\bsuerte con\b",
    r"\bdos bendis\b",
    r"\bdos bendiciones\b",
]

# Contextos que pueden significar que la bendición NO es
# un sorteo abierto a los espectadores.
DIRECT_GIFT_PATTERNS = [
    r"\ble voy a regalar\b",
    r"\ble regalo\b",
    r"\bregalarle\b",
    r"\ba la persona afectada\b",
    r"\ba esta persona\b",
    r"\bse la regalo\b",
    r"\ble voy a dar\b",
]


# =========================================================
# COMPILAR
# =========================================================

WELKIN_REGEX = [
    re.compile(x, re.IGNORECASE)
    for x in WELKIN_PATTERNS
]

GIVEAWAY_REGEX = [
    re.compile(x, re.IGNORECASE)
    for x in GIVEAWAY_PATTERNS
]

ENTRY_REGEX = [
    re.compile(x, re.IGNORECASE)
    for x in ENTRY_PATTERNS
]

AUDIENCE_WELKIN_REGEX = [
    re.compile(x, re.IGNORECASE)
    for x in AUDIENCE_WELKIN_PATTERNS
]

DIRECT_GIFT_REGEX = [
    re.compile(x, re.IGNORECASE)
    for x in DIRECT_GIFT_PATTERNS
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
# OBTENER TRANSCRIPCIONES
# =========================================================

def get_transcripts():
    """
    Revisa TODAS las transcripciones obtenidas.

    No depende de transcript_analysis.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.title,
                    c.channel_name,
                    vt.transcript_text

                FROM video_transcripts vt

                JOIN videos v
                    ON v.id = vt.video_id

                JOIN channels c
                    ON c.id = v.channel_id

                WHERE vt.status = 'OBTENIDA'

                AND vt.transcript_text IS NOT NULL

                ORDER BY v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# UTILIDADES
# =========================================================

def has_match(text, regex_list):

    return any(
        regex.search(text)
        for regex in regex_list
    )


def get_matches(text, regex_list):

    results = []

    for regex in regex_list:

        for match in regex.finditer(text):

            value = match.group(0)

            if value.lower() not in [
                x.lower()
                for x in results
            ]:

                results.append(value)

    return results


# =========================================================
# BUSCAR CONTEXTOS WELKIN
# =========================================================

def find_welkin_contexts(text):

    contexts = []

    for regex in WELKIN_REGEX:

        for match in regex.finditer(text):

            start = max(
                0,
                match.start() - CONTEXT_BEFORE
            )

            end = min(
                len(text),
                match.end() + CONTEXT_AFTER
            )

            context = text[start:end]

            contexts.append({
                "term": match.group(0),
                "context": context
            })

    return contexts


# =========================================================
# ANALIZAR CONTEXTO
# =========================================================

def analyze_context(item):

    context = item["context"]

    giveaway_matches = get_matches(
        context,
        GIVEAWAY_REGEX
    )

    entry_matches = get_matches(
        context,
        ENTRY_REGEX
    )

    audience_matches = get_matches(
        context,
        AUDIENCE_WELKIN_REGEX
    )

    direct_gift_matches = get_matches(
        context,
        DIRECT_GIFT_REGEX
    )

    score = 0

    # Término Welkin/Bendi.
    score += 2

    # Lenguaje explícito de sorteo.
    if giveaway_matches:
        score += 4

    # UID / servidor / comentario / etc.
    if entry_matches:
        score += 2

    # Patrón habitual de Welkin ofrecida
    # a la audiencia del canal.
    if len(audience_matches) >= 2:
        score += 4

    # Regalo dirigido a una persona concreta.
    if direct_gift_matches:
        score -= 5

    return {
        "term": item["term"],

        "score": score,

        "giveaway_matches":
            giveaway_matches,

        "entry_matches":
            entry_matches,

        "audience_matches":
            audience_matches,

        "direct_gift_matches":
            direct_gift_matches,

        "context":
            context,
    }


# =========================================================
# CLASIFICAR VIDEO
# =========================================================

def classify_video(text):

    raw_contexts = find_welkin_contexts(
        text
    )

    if not raw_contexts:

        return {
            "classification": "SIN_WELKIN",
            "score": 0,
            "evidence": [],
        }

    evidence = []

    best_score = 0

    for item in raw_contexts:

        analyzed = analyze_context(
            item
        )

        evidence.append(
            analyzed
        )

        best_score = max(
            best_score,
            analyzed["score"]
        )

    # =====================================================
    # DECISIÓN
    # =====================================================

    # 8 puntos:
    #
    # Welkin             +2
    # lenguaje sorteo    +4
    # requisitos         +2
    #
    # Es la evidencia ideal.

    if best_score >= 8:

        classification = (
            "WELKIN_FUERTE"
        )

    # Welkin + lenguaje explícito de sorteo,
    # aunque no hayamos detectado UID/servidor.

    elif best_score >= 6:

        classification = (
            "WELKIN_PROBABLE"
        )

    else:

        classification = (
            "MENCION_WELKIN"
        )

    return {
        "classification":
            classification,

        "score":
            best_score,

        "evidence":
            evidence,
    }


# =========================================================
# MOSTRAR
# =========================================================

def print_result(
    channel,
    title,
    result
):

    if (
        result["classification"]
        == "SIN_WELKIN"
    ):
        return

    print()
    print("=" * 70)

    print(
        channel,
        "|",
        title
    )

    print(
        "Clasificación:",
        result["classification"]
    )

    print(
        "Score:",
        result["score"]
    )

    print("-" * 70)

    for index, evidence in enumerate(
        result["evidence"],
        start=1
    ):

        print(
            f"Evidencia {index}"
        )

        print(
            "Término:",
            evidence["term"]
        )

        print(
            "Score:",
            evidence["score"]
        )

        print(
            "Sorteo:",
            evidence["giveaway_matches"]
        )

        print(
            "Entrada:",
            evidence["entry_matches"]
        )

        print(
            "Audiencia:",
            evidence["audience_matches"]
        )

        print(
            "Regalo directo:",
            evidence[
                "direct_gift_matches"
            ]
        )

        print(
            "Contexto:"
        )

        print(
            evidence["context"]
        )

        print("-" * 70)

def save_analysis(
    video_id,
    result
):
    """
    Guarda o actualiza el resultado del
    detector específico de Welkin.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO welkin_analysis (
                    video_id,
                    classification,
                    score,
                    evidence,
                    detector_version,
                    analyzed_at
                )

                VALUES (
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

                    score =
                        EXCLUDED.score,

                    evidence =
                        EXCLUDED.evidence,

                    detector_version =
                        EXCLUDED.detector_version,

                    analyzed_at =
                        CURRENT_TIMESTAMP
                """,

                (
                    video_id,

                    result[
                        "classification"
                    ],

                    result[
                        "score"
                    ],

                    json.dumps(
                        result["evidence"],
                        ensure_ascii=False
                    ),

                    DETECTOR_VERSION,
                )
            )

        conn.commit()



# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)

    print(
        "DETECTOR ESPECÍFICO DE WELKIN"
    )

    print(
        "Versión:",
        DETECTOR_VERSION
    )

    print("=" * 70)

    transcripts = get_transcripts()

    print(
        "Transcripciones obtenidas:",
        len(transcripts)
    )

    counters = {
        "WELKIN_FUERTE": 0,
        "WELKIN_PROBABLE": 0,
        "MENCION_WELKIN": 0,
        "SIN_WELKIN": 0,
    }

    results = []

    for row in transcripts:

        (
            video_id,
            title,
            channel,
            transcript_text
        ) = row

        result = classify_video(
            transcript_text
        )

        save_analysis(
            video_id,
            result
        )

        counters[
            result["classification"]
        ] += 1

        results.append(
            (
                video_id,
                channel,
                title,
                result
            )
        )

        print_result(
            channel,
            title,
            result
        )

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    for key, value in counters.items():

        print(
            f"{key:18}: {value}"
        )


if __name__ == "__main__":
    main()
