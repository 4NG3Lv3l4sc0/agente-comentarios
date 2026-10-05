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
    "qwen3:1.7b"
)

ANALYZER_VERSION = "2.0"

OLLAMA_TIMEOUT = 300

MAX_FRAGMENTS = 3
FRAGMENT_RADIUS = 250

# Procesamos estas clases.
TARGET_CLASSES = (
    "CANDIDATO_FUERTE",
    "DUDOSO",
)

# Si ya fue procesado por esta versión/modelo,
# no volvemos a gastar CPU.
SKIP_PROCESSED = True


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
# CREAR TABLA DE RESULTADOS OLLAMA
# =========================================================

def ensure_table():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS
                ollama_giveaway_analysis
                (
                    id BIGSERIAL PRIMARY KEY,

                    video_id BIGINT NOT NULL,

                    model_name TEXT NOT NULL,
                    analyzer_version TEXT NOT NULL,

                    is_giveaway BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    giveaway_type TEXT,

                    has_welkin BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    has_big_giveaway BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    prize TEXT,

                    quantity INTEGER,

                    prize_value NUMERIC,

                    currency TEXT,

                    requires_uid BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    requires_server BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    requires_comment BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    requires_subscription BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    requires_like BOOLEAN NOT NULL
                        DEFAULT FALSE,

                    other_requirements JSONB
                        DEFAULT '[]'::jsonb,

                    active_status TEXT,

                    confidence NUMERIC,

                    review_required BOOLEAN NOT NULL
                        DEFAULT TRUE,

                    local_score INTEGER NOT NULL
                        DEFAULT 0,

                    ollama_time_seconds NUMERIC,

                    prompt_tokens INTEGER,

                    output_tokens INTEGER,

                    tokens_per_second NUMERIC,

                    created_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),

                    updated_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),

                    UNIQUE (
                        video_id,
                        model_name,
                        analyzer_version
                    )
                )
                """
            )

        conn.commit()


# =========================================================
# OBTENER VIDEOS CANDIDATOS
# =========================================================

def get_candidates():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    v.id,
                    v.youtube_video_id,
                    v.title,
                    c.channel_name,
                    vt.transcript_text,
                    ga.classification,
                    ga.global_score

                FROM videos v

                JOIN channels c
                    ON c.id = v.channel_id

                JOIN video_transcripts vt
                    ON vt.video_id = v.id

                JOIN global_analysis ga
                    ON ga.video_id = v.id

                WHERE
                    ga.classification  = ANY(%s)

                ORDER BY
                    ga.global_score DESC,
                    v.published_at DESC
                """,
                (
                    list(TARGET_CLASSES),
                )
            )

            return cur.fetchall()


# =========================================================
# ¿YA FUE PROCESADO?
# =========================================================

def already_processed(video_id):

    if not SKIP_PROCESSED:
        return False

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT 1

                FROM ollama_giveaway_analysis

                WHERE
                    video_id = %s
                    AND model_name = %s
                    AND analyzer_version = %s

                LIMIT 1
                """,
                (
                    video_id,
                    OLLAMA_MODEL,
                    ANALYZER_VERSION,
                )
            )

            return (
                cur.fetchone()
                is not None
            )


# =========================================================
# NORMALIZAR TEXTO
# =========================================================

def normalize(text):

    if not text:
        return ""

    text = text.lower()

    text = (
        text
        .replace("í", "i")
        .replace("ó", "o")
        .replace("á", "a")
        .replace("é", "e")
        .replace("ú", "u")
        .replace("ü", "u")
    )

    return text


# =========================================================
# DETECTOR LOCAL DE REQUISITOS
# =========================================================

def detect_local_requirements(text):

    original = text or ""
    normalized = normalize(original)

    result = {
        "uid": False,
        "server": False,
        "comment": False,
        "subscription": False,
        "like": False,
        "other": [],
        "score": 0,
        "participation_context_found": False,
        "participation_contexts": [],
    }

    # =====================================================
    # 1. BUSCAR CONTEXTOS DE PARTICIPACIÓN
    # =====================================================

    participation_patterns = [
        r"\bpara participar\b",
        r"\bpara poder participar\b",
        r"\bsi quereis participar\b",
        r"\bsi quieres participar\b",
        r"\bsi quieren participar\b",
        r"\bparticipar en el sorteo\b",
        r"\bparticipar del sorteo\b",
        r"\bpara entrar al sorteo\b",
        r"\bpara entrar en el sorteo\b",
        r"\bentrar al sorteo\b",
        r"\bentrar en el sorteo\b",
        r"\brequisitos para participar\b",
        r"\brequisitos del sorteo\b",
        r"\bcondiciones para participar\b",
        r"\bcomo participar\b",
    ]

    matches = []

    for pattern in participation_patterns:

        for match in re.finditer(
            pattern,
            normalized
        ):

            matches.append(
                (
                    match.start(),
                    match.end()
                )
            )

    # =====================================================
    # 2. SI NO HAY CONTEXTO DE PARTICIPACIÓN
    #
    # NO asumimos que like/suscripción/UID son requisitos.
    # =====================================================

    if not matches:

        return result

    result[
        "participation_context_found"
    ] = True

    # =====================================================
    # 3. CREAR VENTANAS ALREDEDOR DE "PARA PARTICIPAR"
    # =====================================================

    REQUIREMENT_BEFORE = 150
    REQUIREMENT_AFTER = 700

    contexts = []

    for start_match, end_match in matches:

        start = max(
            0,
            start_match - REQUIREMENT_BEFORE
        )

        end = min(
            len(normalized),
            end_match + REQUIREMENT_AFTER
        )

        context = normalized[
            start:end
        ]

        contexts.append(
            context
        )

    result[
        "participation_contexts"
    ] = contexts

    # Unimos únicamente las ventanas relacionadas
    # con participación.
    participation_text = "\n".join(
        contexts
    )

    # =====================================================
    # 4. UID
    # =====================================================

    uid_patterns = [
        r"\buid\b",
        r"\bu\s*i\s*d\b",
        r"\btu id\b",
        r"\bel id\b",
    ]

    if any(
        re.search(
            pattern,
            participation_text
        )
        for pattern in uid_patterns
    ):

        result["uid"] = True
        result["score"] += 2

    # =====================================================
    # 5. SERVIDOR / REGIÓN
    # =====================================================

    server_patterns = [
        r"\bservidor\b",
        r"\bserver\b",
        r"\bregion\b",
        r"\bamerica\b",
        r"\beuropa\b",
        r"\basia\b",
    ]

    if any(
        re.search(
            pattern,
            participation_text
        )
        for pattern in server_patterns
    ):

        result["server"] = True
        result["score"] += 1

    # =====================================================
    # 6. COMENTARIO
    # =====================================================

    comment_patterns = [
        r"\bcomentario\b",
        r"\bcomentarios\b",
        r"\bcomentar\b",
        r"\bcomenta\b",
        r"\bcomentad\b",
        r"\bdejad.{0,40}comentario\b",
        r"\bdejar.{0,40}comentario\b",
        r"\bdeja.{0,40}comentario\b",
    ]

    if any(
        re.search(
            pattern,
            participation_text
        )
        for pattern in comment_patterns
    ):

        result["comment"] = True
        result["score"] += 1

    # =====================================================
    # 7. SUSCRIPCIÓN
    # =====================================================

    subscription_patterns = [
        r"\bsuscrito\b",
        r"\bsuscrita\b",
        r"\bsuscrib",
        r"\bsuscripcion\b",
        r"\bsubscrito\b",
        r"\bsubscrib",
    ]

    if any(
        re.search(
            pattern,
            participation_text
        )
        for pattern in subscription_patterns
    ):

        result[
            "subscription"
        ] = True

        result["score"] += 1

    # =====================================================
    # 8. LIKE
    # =====================================================

    like_patterns = [
        r"\blike\b",
        r"\bme gusta\b",
    ]

    if any(
        re.search(
            pattern,
            participation_text
        )
        for pattern in like_patterns
    ):

        result["like"] = True
        result["score"] += 1

    # =====================================================
    # 9. OTROS REQUISITOS
    # =====================================================

    other_patterns = {
        "discord": [
            r"\bdiscord\b",
        ],

        "instagram": [
            r"\binstagram\b",
        ],

        "twitter_x": [
            r"\btwitter\b",
            r"\bx\.com\b",
        ],

        "twitch": [
            r"\btwitch\b",
        ],

        "compartir": [
            r"\bcompartir\b",
            r"\bcomparte\b",
        ],

        "campana": [
            r"\bcampanita\b",
            r"\bcampana\b",
        ],
    }

    for requirement_name, patterns in (
        other_patterns.items()
    ):

        found = any(
            re.search(
                pattern,
                participation_text
            )
            for pattern in patterns
        )

        if found:

            result[
                "other"
            ].append(
                requirement_name
            )

            result[
                "score"
            ] += 1

    return result

# =========================================================
# EVIDENCIAS PARA OLLAMA
# =========================================================

EVIDENCE_PATTERNS = [

    r"\bsorteo\b",
    r"\bsortear\b",
    r"\bsorteando\b",
    r"\bsortearemos\b",
    r"\bsorteamos\b",

    r"\bparticipar\b",
    r"\bparticipa\b",

    r"\bganador\b",
    r"\bganadora\b",
    r"\bganadores\b",

    r"\buid\b",

    r"\bcomentario\b",
    r"\bcomentarios\b",

    r"\bsuscrib",
    r"\blike\b",

    r"\bwelkin\b",
    r"\bbendi\b",
    r"\bbendis\b",
    r"\bbendicion\b",
    r"\bbendiciones\b",

    r"\bpack\b",
    r"\bpacks\b",

    r"\bpremio\b",
    r"\bpremios\b",

    r"\$\s*\d+",
]


EVIDENCE_REGEX = [
    re.compile(
        pattern,
        re.IGNORECASE
    )
    for pattern in EVIDENCE_PATTERNS
]


# =========================================================
# EXTRAER FRAGMENTOS
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

        previous_start, previous_end = (
            merged[-1]
        )

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

    fragments = []

    for start, end in merged:

        fragment = (
            text[start:end]
            .strip()
        )

        if fragment:

            fragments.append(
                fragment
            )

        if (
            len(fragments)
            >= MAX_FRAGMENTS
        ):
            break

    return fragments


# =========================================================
# PROMPT COMPACTO
# =========================================================

def build_prompt(
    channel,
    title,
    fragments
):

    evidence = ""

    for index, fragment in enumerate(
        fragments,
        start=1
    ):

        evidence += (
            f"\n[{index}] "
            f"{fragment}\n"
        )

    return f"""
/no_think

Clasifica sorteos de Genshin Impact.

Usa SOLO la evidencia.
No inventes datos.

CANAL:
{channel}

TITULO:
{title}

EVIDENCIA:
{evidence}

TIPOS:

WELKIN:
Existe una Welkin/Bendicion Lunar/bendi/bendis
SORTEADA a la audiencia.

Un regalo directo a una persona NO cuenta
como sorteo Welkin.

GRANDE:
Packs monetarios, dinero, varios premios
u otro premio mayor.

WELKIN_Y_GRANDE:
Hay ambos tipos de sorteo.

NO_SORTEO:
No existe sorteo real.

NO_CLARO:
La evidencia no permite decidir.

IMPORTANTE:
Un sorteo abierto NO significa automáticamente
que sea WELKIN.

has_welkin=true SOLO si una Welkin/Bendicion
forma parte del sorteo.

Para "5 packs de $100":
quantity=5
prize_value=100
currency="USD".

Devuelve SOLO JSON:

{{
  "is_giveaway": true,
  "giveaway_type": "WELKIN_Y_GRANDE",
  "has_welkin": true,
  "has_big_giveaway": true,
  "prize": "texto o null",
  "quantity": null,
  "prize_value": null,
  "currency": null,
  "active_status": "NO_CLARO"
}}
""".strip()


# =========================================================
# EXTRAER JSON
# =========================================================

def extract_json(text):

    if not text:

        raise ValueError(
            "Respuesta vacía de Ollama."
        )

    text = text.strip()

    if "</think>" in text:

        text = text.split(
            "</think>",
            1
        )[1].strip()

    text = (
        text
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

    try:

        return json.loads(text)

    except json.JSONDecodeError:

        pass

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:

        raise ValueError(
            "Ollama no devolvió JSON válido."
        )

    return json.loads(
        text[start:end + 1]
    )


# =========================================================
# OLLAMA
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

        "think": False,

        "format": "json",

        "keep_alive": "10m",

        "options": {
            "temperature": 0,
            "num_predict": 180,
            "num_ctx": 4096,
        }
    }

    data = json.dumps(
        payload
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=data,
        headers={
            "Content-Type":
                "application/json"
        },
        method="POST"
    )

    start = time.time()

    with urllib.request.urlopen(
        request,
        timeout=OLLAMA_TIMEOUT
    ) as response:

        raw = response.read()

    elapsed = (
        time.time()
        - start
    )

    response_data = json.loads(
        raw.decode("utf-8")
    )

    content = (
        response_data
        .get("message", {})
        .get("content", "")
    )

    result = extract_json(
        content
    )

    eval_count = response_data.get(
        "eval_count",
        0
    )

    eval_duration = response_data.get(
        "eval_duration",
        0
    )

    prompt_tokens = response_data.get(
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
        "prompt_tokens": prompt_tokens,
        "output_tokens": eval_count,
        "tokens_per_second":
            tokens_per_second,
    }

    return result, metrics


# =========================================================
# SEÑALES LOCALES DEL TIPO DE SORTEO
# =========================================================

def detect_giveaway_signals(text):

    normalized = normalize(
        text or ""
    )

    signals = {
        "has_welkin_term": False,
        "has_big_prize_term": False,
        "has_giveaway_action": False,
        "has_participation_action": False,
    }

    # -----------------------------------------------------
    # WELKIN / BENDICIÓN
    # -----------------------------------------------------

    welkin_patterns = [
        r"\bwelkin\b",
        r"\bbendi\b",
        r"\bbendis\b",
        r"\bbendicion lunar\b",
        r"\bbendiciones lunares\b",
    ]

    signals["has_welkin_term"] = any(
        re.search(
            pattern,
            normalized
        )
        for pattern in welkin_patterns
    )

    # -----------------------------------------------------
    # PREMIO GRANDE
    # -----------------------------------------------------

    big_patterns = [
        r"\$\s*\d+",
        r"\b\d+\s*dolares\b",
        r"\bpacks?\s+de\s+\$?\s*\d+",
        r"\btarjeta.{0,30}\$?\s*\d+",
    ]

    signals["has_big_prize_term"] = any(
        re.search(
            pattern,
            normalized
        )
        for pattern in big_patterns
    )

    # -----------------------------------------------------
    # ACCIÓN DE SORTEAR
    # -----------------------------------------------------

    giveaway_patterns = [
        r"\bsorteo\b",
        r"\bsortear\b",
        r"\bsorteamos\b",
        r"\bsorteando\b",
        r"\bsortearemos\b",
        r"\bganador\b",
        r"\bganadores\b",
    ]

    signals["has_giveaway_action"] = any(
        re.search(
            pattern,
            normalized
        )
        for pattern in giveaway_patterns
    )

    # -----------------------------------------------------
    # PARTICIPACIÓN
    # -----------------------------------------------------

    participation_patterns = [
        r"\bpara participar\b",
        r"\bparticipar en\b",
        r"\bparticipa en\b",
        r"\bpara entrar\b",
    ]

    signals["has_participation_action"] = any(
        re.search(
            pattern,
            normalized
        )
        for pattern in participation_patterns
    )

    return signals


# =========================================================
# NORMALIZAR RESULTADO DE OLLAMA
# =========================================================

def normalize_ollama_result(result):

    allowed_types = {
        "WELKIN",
        "GRANDE",
        "WELKIN_Y_GRANDE",
        "NO_SORTEO",
        "NO_CLARO",
    }

    giveaway_type = result.get(
        "giveaway_type",
        "NO_CLARO"
    )

    if giveaway_type not in allowed_types:

        giveaway_type = "NO_CLARO"

    result[
        "giveaway_type"
    ] = giveaway_type

    result[
        "is_giveaway"
    ] = bool(
        result.get(
            "is_giveaway",
            False
        )
    )

    result[
        "has_welkin"
    ] = bool(
        result.get(
            "has_welkin",
            False
        )
    )

    result[
        "has_big_giveaway"
    ] = bool(
        result.get(
            "has_big_giveaway",
            False
        )
    )

    if giveaway_type == "WELKIN":

        result["has_welkin"] = True
        result["has_big_giveaway"] = False

    elif giveaway_type == "GRANDE":

        result["has_welkin"] = False
        result["has_big_giveaway"] = True

    elif giveaway_type == "WELKIN_Y_GRANDE":

        result["has_welkin"] = True
        result["has_big_giveaway"] = True

    elif giveaway_type == "NO_SORTEO":

        result["is_giveaway"] = False

    return result


# =========================================================
# VALIDACIÓN CONTRA EVIDENCIA REAL
# =========================================================

def enforce_evidence(
    result,
    signals
):

    # -----------------------------------------------------
    # GRANDE
    #
    # Qwen NO puede inventar un premio monetario.
    # -----------------------------------------------------

    if not signals[
        "has_big_prize_term"
    ]:

        result[
            "has_big_giveaway"
        ] = False

        # El premio grande inventado se elimina.
        if result.get(
            "giveaway_type"
        ) in {
            "GRANDE",
            "WELKIN_Y_GRANDE",
        }:

            result["prize"] = None
            result["quantity"] = None
            result["prize_value"] = None
            result["currency"] = None

    # -----------------------------------------------------
    # WELKIN
    # -----------------------------------------------------

    if not signals[
        "has_welkin_term"
    ]:

        result[
            "has_welkin"
        ] = False

    # -----------------------------------------------------
    # RECONSTRUIR TIPO
    # -----------------------------------------------------

    has_welkin = result.get(
        "has_welkin",
        False
    )

    has_big = result.get(
        "has_big_giveaway",
        False
    )

    if has_welkin and has_big:

        result[
            "giveaway_type"
        ] = "WELKIN_Y_GRANDE"

    elif has_big:

        result[
            "giveaway_type"
        ] = "GRANDE"

    elif has_welkin:

        result[
            "giveaway_type"
        ] = "WELKIN"

    else:

        result[
            "giveaway_type"
        ] = "NO_CLARO"

    # -----------------------------------------------------
    # NO basta mencionar una bendi.
    # Necesitamos alguna señal de sorteo/participación.
    # -----------------------------------------------------

    if (
        not signals[
            "has_giveaway_action"
        ]
        and
        not signals[
            "has_participation_action"
        ]
    ):

        result[
            "is_giveaway"
        ] = False

        result[
            "has_welkin"
        ] = False

        result[
            "has_big_giveaway"
        ] = False

        result[
            "giveaway_type"
        ] = "NO_CLARO"

        result[
            "prize"
        ] = None

        result[
            "quantity"
        ] = None

        result[
            "prize_value"
        ] = None

        result[
            "currency"
        ] = None

    return result
# =========================================================
# CONFIANZA LOCAL
# =========================================================

def calculate_confidence(
    ollama_result,
    local_result
):

    score = 0.0

    giveaway_type = (
        ollama_result.get(
            "giveaway_type"
        )
    )

    if ollama_result.get(
        "is_giveaway"
    ):
        score += 0.20

    if giveaway_type in {
        "WELKIN",
        "GRANDE",
        "WELKIN_Y_GRANDE",
    }:
        score += 0.25

    if ollama_result.get(
        "prize"
    ):
        score += 0.15

    if (
        ollama_result.get(
            "quantity"
        )
        is not None
    ):
        score += 0.10

    if (
        ollama_result.get(
            "prize_value"
        )
        is not None
    ):
        score += 0.10

    # Señales deterministas de participación.
    local_score = local_result.get(
        "score",
        0
    )

    score += min(
        local_score * 0.04,
        0.20
    )

    return round(
        min(score, 1.0),
        2
    )


# =========================================================
# GUARDAR RESULTADO
# =========================================================

def save_result(
    video_id,
    result,
    local_result,
    confidence,
    review_required,
    metrics
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO
                    ollama_giveaway_analysis
                (
                    video_id,
                    model_name,
                    analyzer_version,

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

                    local_score,

                    ollama_time_seconds,
                    prompt_tokens,
                    output_tokens,
                    tokens_per_second,

                    updated_at
                )

                VALUES
                (
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s::jsonb,
                    %s,
                    %s, %s,
                    %s,
                    %s, %s, %s, %s,
                    NOW()
                )

                ON CONFLICT
                (
                    video_id,
                    model_name,
                    analyzer_version
                )

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

                    local_score =
                        EXCLUDED.local_score,

                    ollama_time_seconds =
                        EXCLUDED.ollama_time_seconds,

                    prompt_tokens =
                        EXCLUDED.prompt_tokens,

                    output_tokens =
                        EXCLUDED.output_tokens,

                    tokens_per_second =
                        EXCLUDED.tokens_per_second,

                    updated_at =
                        NOW()
                """,

                (
                    video_id,
                    OLLAMA_MODEL,
                    ANALYZER_VERSION,

                    result.get(
                        "is_giveaway",
                        False
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

                    local_result[
                        "uid"
                    ],

                    local_result[
                        "server"
                    ],

                    local_result[
                        "comment"
                    ],

                    local_result[
                        "subscription"
                    ],

                    local_result[
                        "like"
                    ],

                    json.dumps(
                        local_result[
                            "other"
                        ]
                    ),

                    result.get(
                        "active_status",
                        "NO_CLARO"
                    ),

                    confidence,

                    review_required,

                    local_result[
                        "score"
                    ],

                    metrics[
                        "elapsed"
                    ],

                    metrics[
                        "prompt_tokens"
                    ],

                    metrics[
                        "output_tokens"
                    ],

                    metrics[
                        "tokens_per_second"
                    ],
                )
            )

        conn.commit()


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print(
        "ANALIZADOR HÍBRIDO LOCAL - OLLAMA"
    )
    print(
        "Versión:",
        ANALYZER_VERSION
    )
    print(
        "Modelo:",
        OLLAMA_MODEL
    )
    print("=" * 70)

    ensure_table()

    videos = get_candidates()

    print(
        "Candidatos encontrados:",
        len(videos)
    )

    counters = {
        "processed": 0,
        "skipped": 0,
        "errors": 0,
        "review": 0,
    }

    for index, video in enumerate(
        videos,
        start=1
    ):

        (
            video_id,
            youtube_video_id,
            title,
            channel,
            transcript,
            global_class,
            global_score,
        ) = video


        # =================================================
        # CACHÉ
        # =================================================

        if already_processed(
            video_id
        ):

            print(
                "YA ANALIZADO - omitido"
            )

            counters[
                "skipped"
            ] += 1

            continue

        # =================================================
        # DETECTOR LOCAL
        # =================================================

        local_result = (
            detect_local_requirements(
                transcript
            )
        )

        print(
            "Local ->",
            "UID:",
            local_result["uid"],
            "| Server:",
            local_result["server"],
            "| Comentario:",
            local_result["comment"],
            "| Suscripción:",
            local_result["subscription"],
            "| Like:",
            local_result["like"],
        )
        print(
            "Contexto participación:",
            local_result[
                "participation_context_found"
            ]
        )
        if local_result[
            "participation_context_found"
        ]:

            for context_index, context in enumerate(
                local_result[
                    "participation_contexts"
                ],
                start=1
            ):

                print(
                    f"CONTEXTO PARTICIPACIÓN "
                    f"{context_index}:"
                )

                print(context)

                print("-" * 70)

        # =================================================
        # EVIDENCIA
        # =================================================

        fragments = extract_evidence(
            transcript
        )

        if not fragments:

            print(
                "Sin fragmentos relevantes."
            )

            counters[
                "errors"
            ] += 1

            continue

        print(
            "Fragmentos:",
            len(fragments)
        )
        print()
        print("EVIDENCIA ENVIADA A OLLAMA:")

        for fragment_index, fragment in enumerate(
            fragments,
            start=1
        ):
            print("-" * 70)
            print(
                f"FRAGMENTO {fragment_index}"
            )
            print(fragment)

        # =================================================
        # OLLAMA
        # =================================================

        try:

            result, metrics = (
                analyze_with_ollama(
                    channel,
                    title,
                    fragments
                )
            )

            result = (
                normalize_ollama_result(
                    result
                )
            )

            signals = (
                detect_giveaway_signals(
                    transcript
                )
            )

            result = enforce_evidence(
                result,
                signals
            )

        except Exception as error:

            print(
                "ERROR OLLAMA:",
                type(error).__name__,
                str(error)
            )

            counters[
                "errors"
            ] += 1

            continue

        # =================================================
        # CONFIANZA
        # =================================================

        confidence = (
            calculate_confidence(
                result,
                local_result
            )
        )

        review_required = (
            confidence < 0.75
            or result.get(
                "giveaway_type"
            )
            in {
                "NO_CLARO",
                "NO_SORTEO",
            }
        )

        if review_required:

            counters[
                "review"
            ] += 1

        # =================================================
        # MOSTRAR
        # =================================================

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
            ),
            "| Grande:",
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
            ),
            "| Valor:",
            result.get(
                "prize_value"
            ),
            result.get(
                "currency"
            )
        )

        print(
            "Confianza fusionada:",
            confidence
        )

        print(
            "Revisión:",
            review_required
        )

        print(
            "Tiempo:",
            f"{metrics['elapsed']:.2f}s",
            "| Tokens/s:",
            f"{metrics['tokens_per_second']:.2f}"
        )

        # =================================================
        # GUARDAR
        # =================================================

        save_result(
            video_id,
            result,
            local_result,
            confidence,
            review_required,
            metrics
        )

        counters[
            "processed"
        ] += 1

    # =====================================================
    # RESUMEN
    # =====================================================

    print()
    print("=" * 70)
    print(
        "RESUMEN"
    )
    print("=" * 70)

    print(
        "Procesados:",
        counters["processed"]
    )

    print(
        "Ya analizados:",
        counters["skipped"]
    )

    print(
        "Revisión:",
        counters["review"]
    )

    print(
        "Errores:",
        counters["errors"]
    )


if __name__ == "__main__":
    main()

