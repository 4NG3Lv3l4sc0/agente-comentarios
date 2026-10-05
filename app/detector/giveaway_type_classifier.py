import os
import re
import json
import psycopg


# =========================================================
# CONFIGURACIÓN
# =========================================================

CLASSIFIER_VERSION = "1.0"


# =========================================================
# PATRONES
# =========================================================

# ---------- WELKIN ----------

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


# ---------- PREMIOS GRANDES ----------

BIG_PATTERNS = [

    # Packs monetarios
    r"\bpack(?:s)?\s+de\s+\$?\s*\d+",

    # Dinero
    r"\$\s*\d+",

    r"\b\d+\s*(?:d[oó]lares|usd)\b",

    # Recargas
    r"\brecarga(?:s)?\s+de\s+\$?\s*\d+",

    # Protogemas explícitas
    r"\b\d[\d.,]*\s*protogemas\b",

    # Cristales génesis
    r"\b\d[\d.,]*\s*cristales\s+g[eé]nesis\b",
]


# ---------- PALABRAS DE PARTICIPACIÓN ----------

PARTICIPATION_PATTERNS = [
    r"\bsorte[oa]\b",
    r"\bsorteando\b",
    r"\bsortear\b",
    r"\bsortearemos\b",
    r"\bganador(?:a|es)?\b",
    r"\bparticipar\b",
    r"\bpremio\b",
    r"\bgiveaway\b",
]


# =========================================================
# COMPILAR REGEX
# =========================================================

WELKIN_REGEX = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in WELKIN_PATTERNS
]

BIG_REGEX = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in BIG_PATTERNS
]

PARTICIPATION_REGEX = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in PARTICIPATION_PATTERNS
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
# OBTENER VIDEOS
# =========================================================

def get_candidates():
    """
    Procesamos solamente videos cuya transcripción
    ya fue clasificada como POSIBLE_SORTEO.

    No enviamos aquí SIN_SENALES.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.title,
                    c.channel_name

                FROM transcript_analysis ta

                JOIN videos v
                    ON v.id = ta.video_id

                JOIN channels c
                    ON c.id = v.channel_id

                WHERE ta.classification = 'POSIBLE_SORTEO'

                ORDER BY
                    v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# OBTENER EVIDENCIA
# =========================================================

def get_evidence(video_id):
    """
    Recupera las ventanas relevantes que ya encontró
    transcript_detector.py.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    te.score,
                    te.context

                FROM transcript_evidence te

                JOIN transcript_analysis ta
                    ON ta.id = te.analysis_id

                WHERE ta.video_id = %s

                ORDER BY
                    te.score DESC,
                    te.start_seconds ASC
                """,
                (video_id,)
            )

            return cur.fetchall()


# =========================================================
# BUSCAR COINCIDENCIAS
# =========================================================

def find_matches(text, regex_list):
    """
    Devuelve coincidencias únicas.
    """

    matches = []

    for regex in regex_list:

        for match in regex.finditer(text):

            value = match.group(0).strip()

            if value.lower() not in [
                x.lower()
                for x in matches
            ]:
                matches.append(value)

    return matches


# =========================================================
# CLASIFICAR
# =========================================================

def classify(evidence):
    """
    Clasificación conservadora.

    WELKIN_FUERTE:
        Hay evidencia Welkin + lenguaje de participación
        y NO existe señal de premio grande.

    GRANDE:
        Existe evidencia explícita de premio grande.

    TIPO_NO_CLARO:
        El video parece sorteo, pero no podemos decidir
        con seguridad entre las categorías.
    """

    welkin_evidence = []
    big_evidence = []

    welkin_score = 0
    big_score = 0

    participation_found = False

    for detector_score, context in evidence:

        welkin_matches = find_matches(
            context,
            WELKIN_REGEX
        )

        big_matches = find_matches(
            context,
            BIG_REGEX
        )

        participation_matches = find_matches(
            context,
            PARTICIPATION_REGEX
        )

        if participation_matches:
            participation_found = True

        # ---------------------------------------------
        # WELKIN
        # ---------------------------------------------

        if welkin_matches:

            welkin_score += 3

            welkin_evidence.append({
                "matches": welkin_matches,
                "context": context[:1000]
            })

        # ---------------------------------------------
        # GRANDE
        # ---------------------------------------------

        if big_matches:

            big_score += 5

            big_evidence.append({
                "matches": big_matches,
                "context": context[:1000]
            })

    # =================================================
    # DECISIÓN
    # =================================================

    # WELKIN y GRANDE son detecciones INDEPENDIENTES.
    #
    # Un mismo video puede contener:
    #
    # - una Welkin normal
    # - un sorteo grande con reglas adicionales
    #
    # En ese caso queremos conservar AMBOS.

    has_welkin = (
        welkin_score >= 3
        and participation_found
    )

    has_big_giveaway = (
        big_score >= 5
        and participation_found
    )

    # -------------------------------------------------
    # CLASIFICACIÓN RESUMIDA
    # -------------------------------------------------

    if has_welkin and has_big_giveaway:

        giveaway_type = "WELKIN_Y_GRANDE"

    elif has_welkin:

        giveaway_type = "WELKIN"

    elif has_big_giveaway:

        giveaway_type = "GRANDE"

    else:

        giveaway_type = "TIPO_NO_CLARO"

    # -------------------------------------------------
    # REVISIÓN
    #
    # La presencia de un sorteo grande obliga a revisar
    # SUS reglas.
    #
    # Esto NO bloquea la participación independiente
    # en la Welkin.
    # -------------------------------------------------

    review_required = (
        has_big_giveaway
        or giveaway_type == "TIPO_NO_CLARO"
    )

    return {
        "giveaway_type": giveaway_type,

        "has_welkin": has_welkin,

        "has_big_giveaway": has_big_giveaway,

        "welkin_score": welkin_score,

        "big_score": big_score,

        "welkin_evidence": welkin_evidence,

        "big_evidence": big_evidence,

        "review_required": review_required,
    }

# =========================================================
# GUARDAR
# =========================================================
def save_analysis(video_id, result):

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO giveaway_type_analysis (
                    video_id,
                    giveaway_type,
                    has_welkin,
                    has_big_giveaway,
                    welkin_score,
                    big_score,
                    welkin_evidence,
                    big_evidence,
                    review_required,
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
                    %s::jsonb,
                    %s::jsonb,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                )

                ON CONFLICT (video_id)

                DO UPDATE SET

                    giveaway_type =
                        EXCLUDED.giveaway_type,

                    has_welkin =
                        EXCLUDED.has_welkin,

                    has_big_giveaway =
                        EXCLUDED.has_big_giveaway,

                    welkin_score =
                        EXCLUDED.welkin_score,

                    big_score =
                        EXCLUDED.big_score,

                    welkin_evidence =
                        EXCLUDED.welkin_evidence,

                    big_evidence =
                        EXCLUDED.big_evidence,

                    review_required =
                        EXCLUDED.review_required,

                    classifier_version =
                        EXCLUDED.classifier_version,

                    analyzed_at =
                        CURRENT_TIMESTAMP
                """,

                (
                    video_id,
                    result["giveaway_type"],
                    result["has_welkin"],
                    result["has_big_giveaway"],
                    result["welkin_score"],
                    result["big_score"],

                    json.dumps(
                        result["welkin_evidence"],
                        ensure_ascii=False
                    ),

                    json.dumps(
                        result["big_evidence"],
                        ensure_ascii=False
                    ),

                    result["review_required"],
                    CLASSIFIER_VERSION,
                )
            )

        conn.commit()


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("CLASIFICADOR LOCAL DE TIPO DE SORTEO")
    print("Versión:", CLASSIFIER_VERSION)
    print("=" * 70)

    candidates = get_candidates()

    print(
        "Videos a analizar:",
        len(candidates)
    )

    counters = {
        "WELKIN": 0,
        "GRANDE": 0,
        "WELKIN_Y_GRANDE": 0,
        "TIPO_NO_CLARO": 0,
    }

    for index, row in enumerate(
        candidates,
        start=1
    ):

        video_id, title, channel = row

        evidence = get_evidence(
            video_id
        )

        result = classify(
            evidence
        )

        save_analysis(
            video_id,
            result
        )

        counters[
            result["giveaway_type"]
        ] += 1

        print()
        print(
            f"[{index}/{len(candidates)}]"
        )

        print(
            channel,
            "|",
            title
        )

        print(
            "Tipo:",
            result["giveaway_type"]
        )

        print(
            "Welkin score:",
            result["welkin_score"]
        )

        print(
            "Grande score:",
            result["big_score"]
        )

        print(
            "Revisión:",
            result["review_required"]
        )

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    for key, value in counters.items():

        print(
            f"{key:16}: {value}"
        )


if __name__ == "__main__":
    main()

