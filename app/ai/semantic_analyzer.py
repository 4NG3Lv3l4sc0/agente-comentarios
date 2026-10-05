import os
import json
import psycopg
from openai import OpenAI

# =========================================================
# CONFIGURACIÓN
# =========================================================

EXTRACTOR_VERSION = "1.0"

MAX_CONTEXT_CHARS = 12000

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.6-luna"
)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
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


# =========================================================
# OBTENER CANDIDATOS
# =========================================================

def get_candidates():
    """
    Obtiene videos que:

    1. El clasificador global considera interesantes.
    2. Tienen evidencia de transcripción.
    3. Todavía no tienen extracción semántica.

    Por ahora procesamos:
        CANDIDATO_FUERTE
        DUDOSO
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
                    ga.classification,
                    ga.global_score

                FROM global_analysis ga

                JOIN videos v
                    ON v.id = ga.video_id

                JOIN channels c
                    ON c.id = v.channel_id

                JOIN transcript_analysis ta
                    ON ta.video_id = v.id

                LEFT JOIN giveaway_extraction ge
                    ON ge.video_id = v.id

                WHERE ga.classification IN (
                    'CANDIDATO_FUERTE',
                    'DUDOSO'
                )

                AND ta.classification IN (
                    'POSIBLE_SORTEO',
                    'DUDOSO'
                )

                AND ge.id IS NULL

                ORDER BY
                    ga.global_score DESC,
                    v.published_at DESC
                """
            )

            return cur.fetchall()


# =========================================================
# OBTENER EVIDENCIA
# =========================================================

def get_evidence(video_id):
    """
    Recupera las ventanas relevantes encontradas por
    transcript_detector.py.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    te.start_seconds,
                    te.end_seconds,
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
# CONSTRUIR CONTEXTO
# =========================================================

def build_context(evidence):
    """
    Convierte las ventanas de evidencia en un único bloque
    preparado para el análisis semántico.
    """

    blocks = []

    total_chars = 0

    for (
        start_seconds,
        end_seconds,
        score,
        context
    ) in evidence:

        block = (
            f"\n"
            f"[VENTANA "
            f"{float(start_seconds):.1f}s"
            f" - "
            f"{float(end_seconds):.1f}s"
            f" | score={score}]\n"
            f"{context}\n"
        )

        if (
            total_chars + len(block)
            > MAX_CONTEXT_CHARS
        ):
            break

        blocks.append(block)

        total_chars += len(block)

    return "\n".join(blocks)


# =========================================================
# ESQUEMA ESPERADO
# =========================================================

def empty_result():
    """
    Estructura estándar de extracción.

    NO_DETECTADO significa que la evidencia disponible
    no permite afirmar Sí o No.
    """

    return {
        "is_giveaway": None,

        "giveaway_type": "NO_DETECTADO",

        "prize_name": None,

        "prize_quantity": None,

        "prize_value": None,

        "prize_currency": None,

        "comment_required": "NO_DETECTADO",

        "uid_required": "NO_DETECTADO",

        "server_required": "NO_DETECTADO",

        "subscription_required": "NO_DETECTADO",

        "like_required": "NO_DETECTADO",

        "winner_count": None,

        "restrictions": [],

        "confidence": 0.0,

        "review_required": True,

        "reason": None,
    }


# =========================================================
# VALIDACIÓN
# =========================================================

def normalize_requirement(value):
    """
    Solo permitimos tres estados.
    """

    if value is True:
        return "SI"

    if value is False:
        return "NO"

    if value is None:
        return "NO_DETECTADO"

    value = str(value).strip().upper()

    if value in (
        "SI",
        "SÍ",
        "YES",
        "TRUE"
    ):
        return "SI"

    if value in (
        "NO",
        "FALSE"
    ):
        return "NO"

    return "NO_DETECTADO"


def validate_result(result):
    """
    Normaliza la respuesta antes de guardarla.
    """

    base = empty_result()

    if not isinstance(result, dict):
        return base

    for key in base:
        if key in result:
            base[key] = result[key]

    # -----------------------------------------------------
    # Tipo de giveaway
    # -----------------------------------------------------

    allowed_types = {
        "WELKIN",
        "GRANDE",
        "OTRO",
        "NO_DETECTADO"
    }

    giveaway_type = str(
        base["giveaway_type"]
    ).upper()

    if giveaway_type not in allowed_types:
        giveaway_type = "NO_DETECTADO"

    base["giveaway_type"] = giveaway_type

    # -----------------------------------------------------
    # Requisitos
    # -----------------------------------------------------

    requirement_fields = [
        "comment_required",
        "uid_required",
        "server_required",
        "subscription_required",
        "like_required",
    ]

    for field in requirement_fields:
        base[field] = normalize_requirement(
            base[field]
        )

    # -----------------------------------------------------
    # Confianza
    # -----------------------------------------------------

    try:
        confidence = float(
            base["confidence"]
        )
    except (TypeError, ValueError):
        confidence = 0.0

    confidence = max(
        0.0,
        min(1.0, confidence)
    )

    base["confidence"] = confidence

    # -----------------------------------------------------
    # Restricciones
    # -----------------------------------------------------

    if not isinstance(
        base["restrictions"],
        list
    ):
        base["restrictions"] = []

    # -----------------------------------------------------
    # Revisión manual
    # -----------------------------------------------------

    if confidence < 0.85:
        base["review_required"] = True

    if base["is_giveaway"] is not True:
        base["review_required"] = True

    if base["giveaway_type"] == "NO_DETECTADO":
        base["review_required"] = True

    return base


# =========================================================
# GUARDAR RESULTADO
# =========================================================

def save_result(
    video_id,
    result,
    evidence_text
):
    """
    Guarda la extracción semántica.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO giveaway_extraction (
                    video_id,
                    is_giveaway,
                    giveaway_type,
                    prize_name,
                    prize_quantity,
                    prize_value,
                    prize_currency,
                    comment_required,
                    uid_required,
                    server_required,
                    subscription_required,
                    like_required,
                    winner_count,
                    confidence,
                    review_required,
                    evidence,
                    extractor_version,
                    extracted_at
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

                    is_giveaway =
                        EXCLUDED.is_giveaway,

                    giveaway_type =
                        EXCLUDED.giveaway_type,

                    prize_name =
                        EXCLUDED.prize_name,

                    prize_quantity =
                        EXCLUDED.prize_quantity,

                    prize_value =
                        EXCLUDED.prize_value,

                    prize_currency =
                        EXCLUDED.prize_currency,

                    comment_required =
                        EXCLUDED.comment_required,

                    uid_required =
                        EXCLUDED.uid_required,

                    server_required =
                        EXCLUDED.server_required,

                    subscription_required =
                        EXCLUDED.subscription_required,

                    like_required =
                        EXCLUDED.like_required,

                    winner_count =
                        EXCLUDED.winner_count,

                    confidence =
                        EXCLUDED.confidence,

                    review_required =
                        EXCLUDED.review_required,

                    evidence =
                        EXCLUDED.evidence,

                    extractor_version =
                        EXCLUDED.extractor_version,

                    extracted_at =
                        CURRENT_TIMESTAMP
                """,

                (
                    video_id,

                    result["is_giveaway"],

                    result["giveaway_type"],

                    result["prize_name"],

                    result["prize_quantity"],

                    result["prize_value"],

                    result["prize_currency"],

                    result["comment_required"],

                    result["uid_required"],

                    result["server_required"],

                    result["subscription_required"],

                    result["like_required"],

                    result["winner_count"],

                    result["confidence"],

                    result["review_required"],

                    evidence_text,

                    EXTRACTOR_VERSION,
                )
            )

        conn.commit()


# =========================================================
# ANALIZADOR SEMÁNTICO
# =========================================================

def semantic_analysis(
    title,
    channel,
    context
):
    """
    Analiza semánticamente las ventanas relevantes
    de la transcripción.

    El modelo debe distinguir:

    - Sorteo principal del video.
    - Premios incidentales.
    - Premios asociados a retos.
    - Sorteos de directos.
    - Campañas distribuidas entre varios videos.
    """

    system_prompt = """
Eres un analizador de sorteos de videos de Genshin Impact.

Tu tarea es determinar si la evidencia contiene un sorteo
en el que un espectador del video puede participar.

Debes analizar el SIGNIFICADO del texto, no limitarte a
buscar palabras.

REGLAS IMPORTANTES:

1. Identifica el sorteo principal disponible para los
   espectadores del video.

2. No confundas regalos incidentales con el sorteo
   principal.

Ejemplo:

"Cada vez que pierda un 50/50 regalaré una bendición
a la persona afectada"

NO necesariamente es el sorteo principal del video.

3. Si existe una campaña con varios premios distribuidos
   entre varios videos, prize_quantity debe representar
   únicamente la cantidad sorteada EN ESTE VIDEO.

Ejemplo:

"Voy a sortear cinco packs de $100 en los próximos videos.
Comenzamos en este y anunciaremos al primer ganador."

Resultado:

prize_quantity = 1
winner_count = 1

No prize_quantity = 5.

4. giveaway_type solamente puede ser:

WELKIN
GRANDE
OTRO
NO_DETECTADO

WELKIN:
Bendición Lunar, Welkin Moon, bendi, bendis o equivalente.

GRANDE:
packs monetarios, grandes cantidades de protogemas,
recargas importantes u otros premios claramente superiores
a una Bendición Lunar.

OTRO:
existe un sorteo pero no pertenece claramente a los
anteriores.

5. Para requisitos usa exclusivamente:

SI
NO
NO_DETECTADO

MUY IMPORTANTE:

Si el texto no menciona un requisito, usa NO_DETECTADO.

No deduzcas "NO" simplemente porque no aparezca.

6. Identifica por separado:

- comentario
- UID
- servidor
- suscripción
- like

7. restrictions debe contener restricciones explícitas,
por ejemplo:

- una participación por persona
- un UID por participante
- región determinada
- edad
- fecha límite
- participación exclusiva de Twitch

No inventes restricciones.

8. confidence debe estar entre 0 y 1.

9. review_required debe ser true si:

- hay ambigüedad,
- existen varios sorteos difíciles de separar,
- las reglas son incompletas,
- existen restricciones importantes,
- no es posible identificar claramente el premio.

10. Devuelve SOLAMENTE JSON válido.
No uses Markdown.
"""

    user_prompt = f"""
CANAL:
{channel}

TÍTULO:
{title}

EVIDENCIA DE TRANSCRIPCIÓN:

{context}

Devuelve exactamente esta estructura JSON:

{{
    "is_giveaway": true,
    "giveaway_type": "WELKIN",
    "prize_name": "Bendición Lunar",
    "prize_quantity": 1,
    "prize_value": null,
    "prize_currency": null,
    "comment_required": "SI",
    "uid_required": "SI",
    "server_required": "NO_DETECTADO",
    "subscription_required": "SI",
    "like_required": "SI",
    "winner_count": 1,
    "restrictions": [],
    "confidence": 0.95,
    "review_required": false,
    "reason": "Explicación breve basada únicamente en la evidencia."
}}
"""

    try:

        response = client.responses.create(
            model=OPENAI_MODEL,

            input=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
        )

        text = response.output_text.strip()

        # Por seguridad, retirar fences si el modelo
        # llegara a incluirlos.
        if text.startswith("```json"):
            text = text[7:]

        elif text.startswith("```"):
            text = text[3:]

        if text.endswith("```"):
            text = text[:-3]

        text = text.strip()

        result = json.loads(text)

        return result

    except Exception as e:

        print(
            "ERROR OPENAI:",
            type(e).__name__,
            str(e)
        )

        result = empty_result()

        result["reason"] = (
            f"Error durante análisis OpenAI: "
            f"{type(e).__name__}"
        )

        return result

# =========================================================
# MOSTRAR RESULTADO
# =========================================================

def print_result(
    title,
    channel,
    result
):

    print()
    print("=" * 70)

    print(channel)
    print(title)

    print("-" * 70)

    print(
        "¿Sorteo?:",
        result["is_giveaway"]
    )

    print(
        "Tipo:",
        result["giveaway_type"]
    )

    print(
        "Premio:",
        result["prize_name"]
    )

    print(
        "Cantidad:",
        result["prize_quantity"]
    )

    print(
        "Valor:",
        result["prize_value"],
        result["prize_currency"]
    )

    print(
        "Comentario:",
        result["comment_required"]
    )

    print(
        "UID:",
        result["uid_required"]
    )

    print(
        "Servidor:",
        result["server_required"]
    )

    print(
        "Suscripción:",
        result["subscription_required"]
    )

    print(
        "Like:",
        result["like_required"]
    )

    print(
        "Ganadores:",
        result["winner_count"]
    )

    print(
        "Confianza:",
        result["confidence"]
    )

    print(
        "Revisión:",
        result["review_required"]
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("ANALIZADOR SEMÁNTICO DE GIVEAWAYS")
    print(
        "Versión:",
        EXTRACTOR_VERSION
    )
    print("=" * 70)

    candidates = get_candidates()

    print(
        "Candidatos pendientes:",
        len(candidates)
    )

    if not candidates:
        return

    for number, row in enumerate(
        candidates,
        start=1
    ):

        (
            video_id,
            youtube_video_id,
            title,
            channel,
            global_classification,
            global_score
        ) = row

        print()
        print(
            f"[{number}/{len(candidates)}]"
        )

        print(
            "Obteniendo evidencia:",
            title
        )

        evidence = get_evidence(
            video_id
        )

        context = build_context(
            evidence
        )

        if not context:

            print(
                "Sin contexto suficiente."
            )

            continue

        # -------------------------------------------------
        # ANÁLISIS SEMÁNTICO
        # -------------------------------------------------

        raw_result = semantic_analysis(
            title,
            channel,
            context
        )

        result = validate_result(
            raw_result
        )

        # -------------------------------------------------
        # MOSTRAR
        # -------------------------------------------------

        print_result(
            title,
            channel,
            result
        )

        # -------------------------------------------------
        # IMPORTANTE
        #
        # Todavía NO guardamos el resultado porque
        # semantic_analysis() aún no está conectado
        # al modelo.
        # -------------------------------------------------

    print()
    print("=" * 70)

    print(
        "Preparación semántica terminada."
    )

    print(
        "Todavía no se escribieron extracciones "
        "en PostgreSQL."
    )


if __name__ == "__main__":
    main()

