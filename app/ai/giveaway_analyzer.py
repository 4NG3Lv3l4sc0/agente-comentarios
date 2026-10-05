import os
import json
import re
import time
import random

from datetime import datetime

import psycopg

from google import genai
from google.genai import types


# =========================================================
# CONFIGURACIÓN
# =========================================================

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY"
)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

ANALYZER_VERSION = "1.0"

MAX_FRAGMENTS = 6
FRAGMENT_RADIUS = 700


# =========================================================
# VALIDAR CONFIGURACIÓN
# =========================================================

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY no está configurada."
    )


# =========================================================
# CLIENTE GEMINI
# =========================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
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
# PALABRAS CLAVE PARA EXTRAER EVIDENCIA
# =========================================================

EVIDENCE_PATTERNS = [

    # Sorteo
    r"\bsorteo\b",
    r"\bsortear\b",
    r"\bsorteando\b",
    r"\bsorteamos\b",
    r"\bsortearemos\b",

    # Ganador
    r"\bganador\b",
    r"\bganadora\b",
    r"\bganadores\b",

    # Participación
    r"\bparticipar\b",
    r"\bparticipando\b",
    r"\bparticipaci[oó]n\b",

    # UID / servidor
    r"\buid\b",
    r"\bservidor\b",
    r"\bserver\b",

    # Comentarios
    r"\bcomentario\b",
    r"\bcomentar\b",
    r"\bcomenta\b",

    # Suscripción
    r"\bsuscrito\b",
    r"\bsuscrita\b",
    r"\bsuscrib",

    # Like
    r"\blike\b",

    # Welkin
    r"\bwelkin\b",
    r"\bbendi\b",
    r"\bbendis\b",
    r"\bbendici[oó]n lunar\b",
    r"\bbendiciones lunares\b",

    # Premios
    r"\bpack\b",
    r"\bpacks\b",
    r"\bpremio\b",
    r"\bpremios\b",

    # Dinero
    r"\$\s*\d+",
    r"\b\d+\s*d[oó]lares\b",
]


EVIDENCE_REGEX = [
    re.compile(
        pattern,
        re.IGNORECASE
    )
    for pattern in EVIDENCE_PATTERNS
]


# =========================================================
# CREAR TABLA GEMINI
# =========================================================

def ensure_table():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS gemini_analysis (

                    id SERIAL PRIMARY KEY,

                    video_id INTEGER NOT NULL
                        REFERENCES videos(id)
                        ON DELETE CASCADE,

                    is_giveaway BOOLEAN,

                    giveaway_type VARCHAR(30),

                    has_welkin BOOLEAN
                        DEFAULT FALSE,

                    has_big_giveaway BOOLEAN
                        DEFAULT FALSE,

                    prize TEXT,

                    quantity INTEGER,

                    prize_value NUMERIC,

                    currency VARCHAR(20),

                    requires_uid BOOLEAN,

                    requires_server BOOLEAN,

                    requires_comment BOOLEAN,

                    requires_subscription BOOLEAN,

                    requires_like BOOLEAN,

                    other_requirements JSONB,

                    active_status VARCHAR(30),

                    confidence NUMERIC,

                    review_required BOOLEAN
                        DEFAULT TRUE,

                    reasoning TEXT,

                    evidence_sent JSONB,

                    model_name VARCHAR(100),

                    analyzer_version VARCHAR(20),

                    analyzed_at TIMESTAMP
                        DEFAULT CURRENT_TIMESTAMP,

                    UNIQUE(video_id)
                )
                """
            )

        conn.commit()


# =========================================================
# OBTENER CANDIDATOS
# =========================================================

def get_candidates():
    """
    Selecciona videos que merecen análisis por IA.

    Por ahora usamos:

    1. CANDIDATO_FUERTE
    2. DUDOSO
    3. Clasificador local GRANDE
    4. WELKIN_Y_GRANDE

    WELKIN_FUERTE puro NO necesita Gemini.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT DISTINCT
                    v.id,
                    v.youtube_video_id,
                    v.title,
                    c.channel_name,
                    vt.transcript_text,

                    COALESCE(
                        ga.classification,
                        ''
                    ) AS global_class,

                    COALESCE(
                        ga.global_score,
                        0
                    ) AS global_score,

                    COALESCE(
                        gta.giveaway_type,
                        ''
                    ) AS local_type,

                    COALESCE(
                        gta.welkin_score,
                        0
                    ) AS welkin_score,

                    COALESCE(
                        gta.big_score,
                        0
                    ) AS big_score

                FROM videos v

                JOIN channels c
                    ON c.id = v.channel_id

                LEFT JOIN video_transcripts vt
                    ON vt.video_id = v.id

                LEFT JOIN global_analysis ga
                    ON ga.video_id = v.id

                LEFT JOIN giveaway_type_analysis gta
                    ON gta.video_id = v.id

                WHERE

                    vt.transcript_text
                        IS NOT NULL

                    AND (

                        ga.classification
                            IN (
                                'CANDIDATO_FUERTE',
                                'DUDOSO'
                            )

                        OR

                        gta.giveaway_type
                            IN (
                                'GRANDE',
                                'WELKIN_Y_GRANDE'
                            )
                    )

                ORDER BY
                    v.id DESC
                """
            )

            return cur.fetchall()


# =========================================================
# EXTRAER FRAGMENTOS
# =========================================================

def extract_evidence(text):

    if not text:
        return []

    matches = []

    for regex in EVIDENCE_REGEX:

        for match in regex.finditer(text):

            matches.append(
                (
                    match.start(),
                    match.end()
                )
            )

    if not matches:
        return []

    matches.sort(
        key=lambda item: item[0]
    )

    fragments = []

    used_ranges = []

    for start_match, end_match in matches:

        start = max(
            0,
            start_match - FRAGMENT_RADIUS
        )

        end = min(
            len(text),
            end_match + FRAGMENT_RADIUS
        )

        # Evitar fragmentos prácticamente iguales.
        overlap = False

        for used_start, used_end in used_ranges:

            if (
                start <= used_end
                and end >= used_start
            ):
                overlap = True
                break

        if overlap:
            continue

        fragment = text[
            start:end
        ].strip()

        if fragment:

            fragments.append(
                fragment
            )

            used_ranges.append(
                (
                    start,
                    end
                )
            )

        if len(fragments) >= MAX_FRAGMENTS:
            break

    return fragments


# =========================================================
# SCHEMA DE RESPUESTA
# =========================================================

RESPONSE_SCHEMA = {

    "type": "object",

    "properties": {

        "is_giveaway": {
            "type": "boolean"
        },

        "giveaway_type": {
            "type": "string",
            "enum": [
                "WELKIN",
                "GRANDE",
                "WELKIN_Y_GRANDE",
                "NO_SORTEO",
                "NO_CLARO"
            ]
        },

        "has_welkin": {
            "type": "boolean"
        },

        "has_big_giveaway": {
            "type": "boolean"
        },

        "prize": {
            "type": [
                "string",
                "null"
            ]
        },

        "quantity": {
            "type": [
                "integer",
                "null"
            ]
        },

        "prize_value": {
            "type": [
                "number",
                "null"
            ]
        },

        "currency": {
            "type": [
                "string",
                "null"
            ]
        },

        "requirements": {

            "type": "object",

            "properties": {

                "uid": {
                    "type": "boolean"
                },

                "server": {
                    "type": "boolean"
                },

                "comment": {
                    "type": "boolean"
                },

                "subscription": {
                    "type": "boolean"
                },

                "like": {
                    "type": "boolean"
                },

                "other": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                },
            },

            "required": [
                "uid",
                "server",
                "comment",
                "subscription",
                "like",
                "other"
            ]
        },

        "active_status": {
            "type": "string",
            "enum": [
                "ACTIVO",
                "FINALIZADO",
                "FUTURO",
                "NO_CLARO"
            ]
        },

        "confidence": {
            "type": "number"
        },

        "review_required": {
            "type": "boolean"
        },

        "reasoning": {
            "type": "string"
        },
    },

    "required": [
        "is_giveaway",
        "giveaway_type",
        "has_welkin",
        "has_big_giveaway",
        "prize",
        "quantity",
        "prize_value",
        "currency",
        "requirements",
        "active_status",
        "confidence",
        "review_required",
        "reasoning"
    ]
}


# =========================================================
# CONSTRUIR PROMPT
# =========================================================

def build_prompt(
    channel,
    title,
    global_class,
    global_score,
    local_type,
    welkin_score,
    big_score,
    fragments
):

    evidence_text = ""

    for index, fragment in enumerate(
        fragments,
        start=1
    ):

        evidence_text += (
            f"\n\n--- FRAGMENTO {index} ---\n"
            f"{fragment}"
        )

    return f"""
Eres un analizador de sorteos de contenido de Genshin Impact.

Tu tarea NO es escribir comentarios.
Tu tarea es determinar si el video contiene un sorteo real
y cuáles son sus condiciones.

Debes basarte EXCLUSIVAMENTE en la evidencia proporcionada.
No inventes condiciones.

IMPORTANTE:

1. "Welkin", "bendición lunar", "bendi" o "bendis"
   pueden referirse a una Bendición Lunar.

2. Una mención a una bendición NO significa necesariamente
   que exista un sorteo.

3. Frases como:
   "le voy a regalar una bendición a esta persona"
   representan un regalo directo, NO un sorteo abierto.

4. Frases dirigidas a toda la audiencia como:
   "tenéis dos bendis"
   o
   "dos bendiciones por apoyar el canal"
   pueden representar sorteos habituales del canal.

5. Un video puede contener AL MISMO TIEMPO:
   - una Welkin,
   - y un sorteo grande.

   En ese caso usa:
   WELKIN_Y_GRANDE.

6. Sorteos de packs de $100, premios monetarios,
   varias Welkin, cuentas, tarjetas u otros premios
   importantes pueden clasificarse como GRANDE.

7. No marques un requisito como verdadero si solamente
   aparece de forma genérica en el video.

   Ejemplo:
   "dejen un like si les gustó el video"

   no necesariamente significa que el like sea requisito
   del sorteo.

8. Diferencia entre:
   - una recomendación general,
   - y una condición explícita para participar.

9. Para active_status:

   ACTIVO:
   la evidencia indica que todavía se puede participar.

   FINALIZADO:
   ya se anunció ganador o claramente terminó.

   FUTURO:
   el sorteo todavía no comienza.

   NO_CLARO:
   no existe evidencia suficiente.

10. confidence debe estar entre 0.0 y 1.0.

11. review_required debe ser TRUE cuando exista
    cualquier ambigüedad importante.


DATOS DEL VIDEO

Canal:
{channel}

Título:
{title}


RESULTADOS DE FILTROS LOCALES

Clasificación global:
{global_class}

Global score:
{global_score}

Clasificador de tipo:
{local_type}

Welkin score:
{welkin_score}

Grande score:
{big_score}


EVIDENCIA DE TRANSCRIPCIÓN
{evidence_text}


Analiza únicamente la información anterior.
"""


# =========================================================
# ANALIZAR CON GEMINI
# =========================================================

def analyze_with_gemini(
    channel,
    title,
    global_class,
    global_score,
    local_type,
    welkin_score,
    big_score,
    fragments
):

    prompt = build_prompt(
        channel,
        title,
        global_class,
        global_score,
        local_type,
        welkin_score,
        big_score,
        fragments
    )

    max_attempts = 5

    for attempt in range(
        1,
        max_attempts + 1
    ):

        try:

            print(
                f"Gemini intento "
                f"{attempt}/{max_attempts}..."
            )

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_json_schema=RESPONSE_SCHEMA,
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini devolvió una respuesta vacía."
                )

            return json.loads(
                response.text
            )

        except Exception as error:

            error_text = str(error)
            error_lower = error_text.lower()

            # =================================================
            # 1. SATURACIÓN DEL MODELO
            # Espera corta: 5 - 10 segundos
            # =================================================

            is_saturation = any(
                value in error_lower
                for value in [
                    "high demand",
                    "high-demand",
                    "overloaded",
                    "model overloaded",
                    "503",
                    "service unavailable",
                ]
            )

            if is_saturation:

                if attempt >= max_attempts:
                    raise

                wait_time = random.uniform(
                    5,
                    10
                )

                print()
                print(
                    "Gemini está saturado."
                )

                print(
                    f"Esperando {wait_time:.1f}s "
                    "antes de reintentar..."
                )

                time.sleep(
                    wait_time
                )

                continue


            # =================================================
            # 2. CUOTA / RATE LIMIT
            # Espera más larga
            # =================================================

            is_quota = any(
                value in error_lower
                for value in [
                    "429",
                    "resource_exhausted",
                    "rate limit",
                    "rate_limit",
                    "quota exceeded",
                ]
            )

            if is_quota:

                if attempt >= max_attempts:
                    raise

                # ---------------------------------------------
                # Buscar retryDelay enviado por Gemini
                # Ejemplo:
                # 'retryDelay': '58s'
                # ---------------------------------------------

                retry_match = re.search(
                    r"'retryDelay':\s*'([\d.]+)s'",
                    error_text
                )

                if retry_match:

                    retry_seconds = float(
                        retry_match.group(1)
                    )

                    # Pequeño margen adicional.
                    wait_time = (
                        retry_seconds + 2
                    )

                else:

                    # Si Gemini no especifica tiempo.
                    wait_time = 40

                print()
                print(
                    "Cuota/rate limit de Gemini "
                    "alcanzado."
                )

                print(
                    f"Esperando {wait_time:.1f}s "
                    "antes de reintentar..."
                )

                time.sleep(
                    wait_time
                )

                continue


            # =================================================
            # 3. OTRO ERROR
            # =================================================

            raise

    raise RuntimeError(
        "No fue posible obtener respuesta de Gemini."
    )



# =========================================================
# GUARDAR RESULTADO
# =========================================================

def save_analysis(
    video_id,
    result,
    fragments
):

    requirements = result.get(
        "requirements",
        {}
    )

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO gemini_analysis (

                    video_id,

                    is_giveaway,
                    giveaway_type,

                    has_welkin,
                    has_big_giveaway,

                    prize,
                    quantity,
                    prize_value,
                    currency,

                    requires_uid,
                    requires_server,
                    requires_comment,
                    requires_subscription,
                    requires_like,

                    other_requirements,

                    active_status,

                    confidence,

                    review_required,

                    reasoning,

                    evidence_sent,

                    model_name,

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
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
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

                    has_welkin =
                        EXCLUDED.has_welkin,

                    has_big_giveaway =
                        EXCLUDED.has_big_giveaway,

                    prize =
                        EXCLUDED.prize,

                    quantity =
                        EXCLUDED.quantity,

                    prize_value =
                        EXCLUDED.prize_value,

                    currency =
                        EXCLUDED.currency,

                    requires_uid =
                        EXCLUDED.requires_uid,

                    requires_server =
                        EXCLUDED.requires_server,

                    requires_comment =
                        EXCLUDED.requires_comment,

                    requires_subscription =
                        EXCLUDED.requires_subscription,

                    requires_like =
                        EXCLUDED.requires_like,

                    other_requirements =
                        EXCLUDED.other_requirements,

                    active_status =
                        EXCLUDED.active_status,

                    confidence =
                        EXCLUDED.confidence,

                    review_required =
                        EXCLUDED.review_required,

                    reasoning =
                        EXCLUDED.reasoning,

                    evidence_sent =
                        EXCLUDED.evidence_sent,

                    model_name =
                        EXCLUDED.model_name,

                    analyzer_version =
                        EXCLUDED.analyzer_version,

                    analyzed_at =
                        CURRENT_TIMESTAMP
                """,

                (
                    video_id,

                    result.get(
                        "is_giveaway"
                    ),

                    result.get(
                        "giveaway_type"
                    ),

                    result.get(
                        "has_welkin",
                        False
                    ),

                    result.get(
                        "has_big_giveaway",
                        False
                    ),

                    result.get(
                        "prize"
                    ),

                    result.get(
                        "quantity"
                    ),

                    result.get(
                        "prize_value"
                    ),

                    result.get(
                        "currency"
                    ),

                    requirements.get(
                        "uid",
                        False
                    ),

                    requirements.get(
                        "server",
                        False
                    ),

                    requirements.get(
                        "comment",
                        False
                    ),

                    requirements.get(
                        "subscription",
                        False
                    ),

                    requirements.get(
                        "like",
                        False
                    ),

                    json.dumps(
                        requirements.get(
                            "other",
                            []
                        ),
                        ensure_ascii=False
                    ),

                    result.get(
                        "active_status"
                    ),

                    result.get(
                        "confidence",
                        0
                    ),

                    result.get(
                        "review_required",
                        True
                    ),

                    result.get(
                        "reasoning"
                    ),

                    json.dumps(
                        fragments,
                        ensure_ascii=False
                    ),

                    GEMINI_MODEL,

                    ANALYZER_VERSION,
                )
            )

        conn.commit()


# =========================================================
# MOSTRAR RESULTADO
# =========================================================

def print_result(
    channel,
    title,
    result
):

    requirements = result.get(
        "requirements",
        {}
    )

    print()
    print("-" * 70)

    print(
        channel
    )

    print(
        title
    )

    print("-" * 70)

    print(
        "¿Sorteo?:",
        result.get(
            "is_giveaway"
        )
    )

    print(
        "Tipo:",
        result.get(
            "giveaway_type"
        )
    )

    print(
        "Welkin:",
        result.get(
            "has_welkin"
        )
    )

    print(
        "Grande:",
        result.get(
            "has_big_giveaway"
        )
    )

    print(
        "Premio:",
        result.get(
            "prize"
        )
    )

    print(
        "Cantidad:",
        result.get(
            "quantity"
        )
    )

    print(
        "Valor:",
        result.get(
            "prize_value"
        ),
        result.get(
            "currency"
        )
    )

    print(
        "UID:",
        requirements.get(
            "uid"
        )
    )

    print(
        "Servidor:",
        requirements.get(
            "server"
        )
    )

    print(
        "Comentario:",
        requirements.get(
            "comment"
        )
    )

    print(
        "Suscripción:",
        requirements.get(
            "subscription"
        )
    )

    print(
        "Like:",
        requirements.get(
            "like"
        )
    )

    print(
        "Otros:",
        requirements.get(
            "other"
        )
    )

    print(
        "Estado:",
        result.get(
            "active_status"
        )
    )

    print(
        "Confianza:",
        result.get(
            "confidence"
        )
    )

    print(
        "Revisión:",
        result.get(
            "review_required"
        )
    )

    print(
        "Razón:",
        result.get(
            "reasoning"
        )
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)

    print(
        "ANALIZADOR GEMINI DE SORTEOS"
    )

    print(
        "Versión:",
        ANALYZER_VERSION
    )

    print(
        "Modelo:",
        GEMINI_MODEL
    )

    print("=" * 70)

    ensure_table()

    candidates = get_candidates()

    print(
        "Videos candidatos:",
        len(candidates)
    )

    counters = {
        "WELKIN": 0,
        "GRANDE": 0,
        "WELKIN_Y_GRANDE": 0,
        "NO_SORTEO": 0,
        "NO_CLARO": 0,
        "ERROR": 0,
    }

    for index, row in enumerate(
        candidates,
        start=1
    ):

        (
            video_id,
            youtube_video_id,
            title,
            channel,
            transcript_text,
            global_class,
            global_score,
            local_type,
            welkin_score,
            big_score,
        ) = row

        print()
        print("=" * 70)

        print(
            f"[{index}/{len(candidates)}]"
        )

        print(
            channel,
            "|",
            title
        )

        fragments = extract_evidence(
            transcript_text
        )

        print(
            "Fragmentos enviados:",
            len(fragments)
        )

        if not fragments:

            print(
                "Sin evidencia suficiente. "
                "No se envía a Gemini."
            )

            counters[
                "NO_CLARO"
            ] += 1

            continue

        try:

            result = analyze_with_gemini(
                channel,
                title,
                global_class,
                global_score,
                local_type,
                welkin_score,
                big_score,
                fragments
            )

            giveaway_type = result.get(
                "giveaway_type",
                "NO_CLARO"
            )

            if giveaway_type not in counters:
                giveaway_type = "NO_CLARO"

            counters[
                giveaway_type
            ] += 1

            save_analysis(
                video_id,
                result,
                fragments
            )

            print_result(
                channel,
                title,
                result
            )

        except Exception as error:

            counters[
                "ERROR"
            ] += 1

            print()
            print(
                "ERROR GEMINI:"
            )

            print(
                type(error).__name__
            )

            print(
                str(error)
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


