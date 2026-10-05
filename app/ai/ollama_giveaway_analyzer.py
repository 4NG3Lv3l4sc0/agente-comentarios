import os
import json
import re
import time
import urllib.request
import urllib.error

import psycopg


# =========================================================
# CONFIGURACIÓN
# =========================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://host.docker.internal:11434"
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen3:4b"
)

ANALYZER_VERSION = "1.0"

# Primera prueba: SOLO este video.
TEST_TITLE = "JUZGUÉ las ODETTE"

MAX_FRAGMENTS = 3

# Radio alrededor de cada coincidencia.
FRAGMENT_RADIUS = 200

# Timeout amplio porque estamos trabajando por CPU.
OLLAMA_TIMEOUT = 300


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
# PALABRAS CLAVE
# =========================================================

EVIDENCE_PATTERNS = [

    # Sorteos
    r"\bsorteo\b",
    r"\bsortear\b",
    r"\bsorteando\b",
    r"\bsortearemos\b",
    r"\bsorteamos\b",

    # Participación
    r"\bparticipar\b",
    r"\bparticipa\b",
    r"\bparticipando\b",

    # Ganadores
    r"\bganador\b",
    r"\bganadora\b",
    r"\bganadores\b",

    # UID / servidor
    r"\buid\b",
    r"\bid\b",
    r"\bservidor\b",
    r"\bserver\b",
    r"\bam[eé]rica\b",
    r"\beuropa\b",
    r"\basia\b",

    # Comentario
    r"\bcomentario\b",
    r"\bcomentarios\b",
    r"\bcomentar\b",

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
    r"\bbendici[oó]n\b",
    r"\bbendiciones\b",
    r"\bbendici[oó]n lunar\b",
    r"\bbendiciones lunares\b",

    # Premios
    r"\bpremio\b",
    r"\bpremios\b",
    r"\bpack\b",
    r"\bpacks\b",

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
# OBTENER VIDEO DE PRUEBA
# =========================================================

def get_test_video():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.youtube_video_id,
                    v.title,
                    c.channel_name,
                    vt.transcript_text

                FROM videos v

                JOIN channels c
                    ON c.id = v.channel_id

                JOIN video_transcripts vt
                    ON vt.video_id = v.id

                WHERE
                    v.title ILIKE %s

                ORDER BY
                    v.published_at DESC

                LIMIT 1
                """,

                (
                    f"%{TEST_TITLE}%",
                )
            )

            return cur.fetchone()


# =========================================================
# EXTRAER FRAGMENTOS RELEVANTES
# =========================================================

def extract_evidence(text):

    if not text:
        return []

    positions = []

    for regex in EVIDENCE_REGEX:

        for match in regex.finditer(text):

            positions.append(
                (
                    match.start(),
                    match.end()
                )
            )

    if not positions:
        return []

    positions.sort(
        key=lambda item: item[0]
    )

    # =====================================================
    # CREAR RANGOS
    # =====================================================

    ranges = []

    for start_match, end_match in positions:

        start = max(
            0,
            start_match - FRAGMENT_RADIUS
        )

        end = min(
            len(text),
            end_match + FRAGMENT_RADIUS
        )

        ranges.append(
            [
                start,
                end
            ]
        )

    # =====================================================
    # UNIR RANGOS SOLAPADOS
    # =====================================================

    merged = []

    for current_start, current_end in ranges:

        if not merged:

            merged.append(
                [
                    current_start,
                    current_end
                ]
            )

            continue

        previous_start, previous_end = merged[-1]

        if current_start <= previous_end:

            merged[-1][1] = max(
                previous_end,
                current_end
            )

        else:

            merged.append(
                [
                    current_start,
                    current_end
                ]
            )

    # =====================================================
    # CONVERTIR EN FRAGMENTOS
    # =====================================================

    fragments = []

    for start, end in merged:

        fragment = text[
            start:end
        ].strip()

        if fragment:

            fragments.append(
                fragment
            )

        if len(fragments) >= MAX_FRAGMENTS:
            break

    return fragments


# =========================================================
# CONSTRUIR PROMPT
# =========================================================

def build_prompt(
    channel,
    title,
    fragments
):

    evidence_text = ""

    for index, fragment in enumerate(
        fragments,
        start=1
    ):

        evidence_text += (
            f"\n\n"
            f"--- FRAGMENTO {index} ---\n"
            f"{fragment}"
        )

    prompt = f"""
/no_think

Eres un clasificador de sorteos en videos de Genshin Impact.

Analiza EXCLUSIVAMENTE la evidencia proporcionada.

No inventes información.

Tu tarea es determinar:

1. Si existe un sorteo real abierto a la audiencia.
2. Si existe una Welkin/Bendición Lunar sorteada.
3. Si existe un sorteo grande.
4. Qué premio se entrega.
5. Cuántos premios existen.
6. Qué requisitos son obligatorios para participar.
7. Si el sorteo parece activo.
8. Qué tan segura es la clasificación.

DEFINICIONES IMPORTANTES:

WELKIN:
Welkin Moon, Bendición Lunar, bendición, bendi o bendis
cuando se entregan mediante un sorteo abierto a espectadores.

NO confundas una Welkin sorteada con un regalo directo.

Ejemplo de REGALO DIRECTO:

"Perdió el 50/50, así que le voy a regalar una bendición."

Eso NO es un sorteo abierto por sí solo.

Ejemplo de SORTEO WELKIN:

"Voy a sortear una bendición entre todos los que están viendo."

Eso SÍ es un sorteo.

GRANDE:
Sorteos de packs monetarios, varios premios, tarjetas,
cuentas, grandes cantidades de dinero u otros premios
superiores a una Welkin habitual.

Un video puede tener AL MISMO TIEMPO:

WELKIN + GRANDE

En ese caso:

giveaway_type = "WELKIN_Y_GRANDE"

TIPOS PERMITIDOS:

"WELKIN"
"GRANDE"
"WELKIN_Y_GRANDE"
"NO_SORTEO"
"NO_CLARO"

IMPORTANTE:

Un sorteo abierto a la audiencia NO significa que sea WELKIN.

has_welkin = true únicamente si la evidencia indica que una
Welkin, Bendición Lunar, bendi o bendis forma parte del sorteo.

Un pack de $100, dinero u otro premio grande sin una Bendición
Lunar sorteada debe clasificarse como GRANDE, no WELKIN_Y_GRANDE.

REQUISITOS:

Solo marca un requisito como true cuando la evidencia indique
que es una condición para participar.

Ejemplo:

"Deja like si te gustó el video"

NO significa necesariamente que el like sea requisito.

Pero:

"Para participar debes estar suscrito, dejar tu UID
y dejar un like"

SÍ establece requisitos.

ESTADO:

"ACTIVO"
"FINALIZADO"
"FUTURO"
"NO_CLARO"

CONFIDENCE:

Número entre 0.0 y 1.0.

REGLA PARA VALOR DEL PREMIO:

"quantity" representa el número de premios.

"prize_value" representa el valor DE CADA PREMIO,
no la suma total.

Ejemplo:

"5 packs de $100"

debe producir:

"quantity": 5
"prize_value": 100
"currency": "USD"

Usa confianza alta únicamente cuando la evidencia sea explícita.

CANAL:
{channel}

TÍTULO:
{title}

EVIDENCIA:
{evidence_text}

Devuelve EXCLUSIVAMENTE un objeto JSON válido.

No uses Markdown.
No uses ```json.
No escribas explicaciones fuera del JSON.

La estructura EXACTA debe ser:

{{
    "is_giveaway": true,
    "giveaway_type": "WELKIN_Y_GRANDE",
    "has_welkin": true,
    "has_big_giveaway": true,
    "prize": "texto o null",
    "quantity": null,
    "prize_value": null,
    "currency": null,
    "requirements": {{
        "uid": false,
        "server": false,
        "comment": false,
        "subscription": false,
        "like": false,
        "other": []
    }},
    "active_status": "NO_CLARO",
    "confidence": 0.0,
    "review_required": true,
    "active_status": "NO_CLARO",
    "confidence": 0.0,
    "review_required": true
}}
"""

    return prompt.strip()


# =========================================================
# EXTRAER JSON DE RESPUESTA
# =========================================================

def extract_json(text):

    if not text:
        raise ValueError(
            "Ollama devolvió una respuesta vacía."
        )

    text = text.strip()

    # Eliminar posibles bloques de thinking.
    if "</think>" in text:

        text = text.split(
            "</think>",
            1
        )[1].strip()

    # Eliminar Markdown accidental.
    text = text.replace(
        "```json",
        ""
    )

    text = text.replace(
        "```",
        ""
    )

    text = text.strip()

    # Intento directo.
    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError:

        pass

    # Buscar el primer objeto JSON.
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:

        raise ValueError(
            "No se encontró un objeto JSON "
            "en la respuesta de Ollama."
        )

    json_text = text[
        start:end + 1
    ]

    return json.loads(
        json_text
    )


# =========================================================
# VALIDAR RESULTADO
# =========================================================

def validate_result(result):

    allowed_types = {
        "WELKIN",
        "GRANDE",
        "WELKIN_Y_GRANDE",
        "NO_SORTEO",
        "NO_CLARO",
    }

    allowed_status = {
        "ACTIVO",
        "FINALIZADO",
        "FUTURO",
        "NO_CLARO",
    }

    giveaway_type = result.get(
        "giveaway_type",
        "NO_CLARO"
    )

    if giveaway_type not in allowed_types:

        result[
            "giveaway_type"
        ] = "NO_CLARO"

        result[
            "review_required"
        ] = True

    active_status = result.get(
        "active_status",
        "NO_CLARO"
    )

    if active_status not in allowed_status:

        result[
            "active_status"
        ] = "NO_CLARO"

        result[
            "review_required"
        ] = True

    # =====================================================
    # BOOLEANOS
    # =====================================================

    for field in [
        "is_giveaway",
        "has_welkin",
        "has_big_giveaway",
        "review_required",
    ]:

        if not isinstance(
            result.get(field),
            bool
        ):

            result[field] = False

            result[
                "review_required"
            ] = True

    # =====================================================
    # REQUIREMENTS
    # =====================================================

    requirements = result.get(
        "requirements"
    )

    if not isinstance(
        requirements,
        dict
    ):

        requirements = {}

    for field in [
        "uid",
        "server",
        "comment",
        "subscription",
        "like",
    ]:

        if not isinstance(
            requirements.get(field),
            bool
        ):

            requirements[field] = False

    if not isinstance(
        requirements.get("other"),
        list
    ):

        requirements[
            "other"
        ] = []

    result[
        "requirements"
    ] = requirements

    # =====================================================
    # CONFIDENCE
    # =====================================================

    try:

        confidence = float(
            result.get(
                "confidence",
                0
            )
        )

    except (
        TypeError,
        ValueError
    ):

        confidence = 0.0

    confidence = max(
        0.0,
        min(
            1.0,
            confidence
        )
    )

    result[
        "confidence"
    ] = confidence

    return result


# =========================================================
# LLAMAR A OLLAMA
# =========================================================

def analyze_with_ollama(
    channel,
    title,
    fragments
):

    prompt = build_prompt(
        channel,
        title,
        fragments
    )

    payload = {

        "model": OLLAMA_MODEL,

        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],

        "stream": False,

        # Lo mantenemos aunque /no_think sea
        # lo que está funcionando correctamente.
        "think": False,

        # Pedimos explícitamente JSON.
        "format": "json",

        "keep_alive": "10m",

        "options": {

            # Queremos respuestas deterministas.
            "temperature": 0,

            # No necesitamos una respuesta enorme.
            "num_predict": 180,

            # Suficiente para los fragmentos seleccionados.
            "num_ctx": 4096,
        }
    }

    data = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(

        f"{OLLAMA_URL}/api/chat",

        data=data,

        headers={
            "Content-Type":
                "application/json"
        },

        method="POST"
    )

    start_time = time.time()

    try:

        with urllib.request.urlopen(
            request,
            timeout=OLLAMA_TIMEOUT
        ) as response:

            raw_response = response.read()

    except urllib.error.URLError as error:

        raise RuntimeError(
            f"No fue posible conectar con Ollama: "
            f"{error}"
        )

    elapsed = (
        time.time()
        - start_time
    )

    response_data = json.loads(
        raw_response.decode(
            "utf-8"
        )
    )

    message = response_data.get(
        "message",
        {}
    )

    content = message.get(
        "content",
        ""
    )

    result = extract_json(
        content
    )

    result = validate_result(
        result
    )

    # =====================================================
    # MÉTRICAS
    # =====================================================

    eval_count = response_data.get(
        "eval_count",
        0
    )

    eval_duration = response_data.get(
        "eval_duration",
        0
    )

    prompt_eval_count = response_data.get(
        "prompt_eval_count",
        0
    )

    if eval_duration:

        tokens_per_second = (
            eval_count
            /
            (
                eval_duration
                / 1_000_000_000
            )
        )

    else:

        tokens_per_second = 0

    metrics = {

        "elapsed": elapsed,

        "prompt_tokens": prompt_eval_count,

        "output_tokens": eval_count,

        "tokens_per_second": tokens_per_second,
    }

    return (
        result,
        metrics,
        content
    )


# =========================================================
# MOSTRAR RESULTADO
# =========================================================

def print_result(
    channel,
    title,
    fragments,
    result,
    metrics
):

    requirements = result.get(
        "requirements",
        {}
    )

    print()
    print("=" * 70)

    print(
        "RESULTADO OLLAMA"
    )

    print("=" * 70)

    print(
        "Canal:",
        channel
    )

    print(
        "Título:",
        title
    )

    print(
        "Modelo:",
        OLLAMA_MODEL
    )

    print(
        "Fragmentos:",
        len(fragments)
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

    print("-" * 70)

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

    print("-" * 70)

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

    print()
    print("=" * 70)

    print(
        "RENDIMIENTO"
    )

    print("=" * 70)

    print(
        "Tiempo:",
        f"{metrics['elapsed']:.2f} s"
    )

    print(
        "Tokens entrada:",
        metrics[
            "prompt_tokens"
        ]
    )

    print(
        "Tokens salida:",
        metrics[
            "output_tokens"
        ]
    )

    print(
        "Tokens/s:",
        f"{metrics['tokens_per_second']:.2f}"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)

    print(
        "ANALIZADOR LOCAL DE SORTEOS - OLLAMA"
    )

    print(
        "Versión:",
        ANALYZER_VERSION
    )

    print(
        "Modelo:",
        OLLAMA_MODEL
    )

    print(
        "Servidor:",
        OLLAMA_URL
    )

    print("=" * 70)

    # =====================================================
    # OBTENER VIDEO
    # =====================================================

    video = get_test_video()

    if not video:

        print(
            "No se encontró el video de prueba."
        )

        return

    (
        video_id,
        youtube_video_id,
        title,
        channel,
        transcript_text,
    ) = video

    print()
    print(
        "Video encontrado:"
    )

    print(
        channel,
        "|",
        title
    )

    print(
        "YouTube ID:",
        youtube_video_id
    )

    # =====================================================
    # EXTRAER EVIDENCIA
    # =====================================================

    fragments = extract_evidence(
        transcript_text
    )

    print(
        "Fragmentos relevantes:",
        len(fragments)
    )

    if not fragments:

        print(
            "No se encontró evidencia relevante."
        )

        return

    # =====================================================
    # MOSTRAR TAMAÑO APROXIMADO
    # =====================================================

    total_chars = sum(
        len(fragment)
        for fragment in fragments
    )

    print(
        "Caracteres enviados:",
        total_chars
    )

    print()
    print(
        "Analizando con Ollama..."
    )

    # =====================================================
    # ANALIZAR
    # =====================================================

    try:

        (
            result,
            metrics,
            raw_content
        ) = analyze_with_ollama(
            channel,
            title,
            fragments
        )

    except Exception as error:

        print()
        print(
            "ERROR OLLAMA:"
        )

        print(
            type(error).__name__
        )

        print(
            str(error)
        )

        return

    # =====================================================
    # RESULTADO
    # =====================================================

    print_result(
        channel,
        title,
        fragments,
        result,
        metrics
    )

    print()
    print("=" * 70)

    print(
        "JSON COMPLETO"
    )

    print("=" * 70)

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )


if __name__ == "__main__":
    main()

