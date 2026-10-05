import os
import psycopg


# =========================================================
# CONFIGURACIÓN
# =========================================================

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen3:1.7b"
)

ANALYZER_VERSION = "2.0"
GAMESANDCHILL_CUTOFF_VIDEO_ID = 88

# =========================================================
# CANALES DE CONFIANZA
# =========================================================
#
# Estos canales NO necesitan confirmación de Ollama.
#
# GamesAndChill:
# Todo video nuevo se considera directamente un sorteo
# de tipo WELKIN.
#
# Las protecciones contra duplicados se mantienen.
# =========================================================

TRUSTED_CHANNELS = {
    "GamesAndChill": {
        "giveaway_type": "WELKIN",
        "prize": "Bendición lunar",
        "requires_uid": True,
        "requires_server": False,
        "confidence": 1.0,
        "review_required": False,
    }
}


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
# RESULTADOS OLLAMA
# =========================================================

def get_ollama_results():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    o.video_id,
                    o.giveaway_type,
                    o.prize,
                    o.requires_uid,
                    o.requires_server,
                    o.confidence,
                    o.review_required,
                    v.title,
                    c.channel_name

                FROM ollama_giveaway_analysis o

                JOIN videos v
                    ON v.id = o.video_id

                JOIN channels c
                    ON c.id = v.channel_id

                WHERE
                    o.model_name = %s
                    AND o.analyzer_version = %s
                    AND o.is_giveaway = TRUE
                    AND o.giveaway_type IN (
                        'WELKIN',
                        'GRANDE',
                        'WELKIN_Y_GRANDE'
                    )
                    AND c.channel_name <> 'GamesAndChill'
                    AND v.is_short = FALSE

                ORDER BY
                    o.confidence DESC
                """,
                (
                    OLLAMA_MODEL,
                    ANALYZER_VERSION,
                )
            )

            return cur.fetchall()


# =========================================================
# VIDEOS DE CANALES DE CONFIANZA
# =========================================================

def get_trusted_channel_videos():

    results = []

    with get_connection() as conn:

        with conn.cursor() as cur:

            for (
                channel_name,
                config
            ) in TRUSTED_CHANNELS.items():

                cur.execute(
                    """
                    SELECT
                        v.id,
                        v.title,
                        c.channel_name

                    FROM videos v

                    JOIN channels c
                        ON c.id = v.channel_id

                    LEFT JOIN giveaways g
                        ON g.video_id = v.id

                    WHERE
                        c.channel_name = %s
                        AND g.id IS NULL
                        AND v.id > %s
                        AND v.is_short = FALSE

                    ORDER BY
                        v.published_at ASC
                    """,
                    (
                        channel_name,
                        GAMESANDCHILL_CUTOFF_VIDEO_ID,
                    )
                )

                rows = cur.fetchall()

                for row in rows:

                    (
                        video_id,
                        title,
                        real_channel_name,
                    ) = row

                    results.append(
                        (
                            video_id,
                            config[
                                "giveaway_type"
                            ],
                            config[
                                "prize"
                            ],
                            config[
                                "requires_uid"
                            ],
                            config[
                                "requires_server"
                            ],
                            config[
                                "confidence"
                            ],
                            config[
                                "review_required"
                            ],
                            title,
                            real_channel_name,
                        )
                    )

    return results


# =========================================================
# CREAR / ACTUALIZAR GIVEAWAY
# =========================================================

def upsert_giveaway(
    video_id,
    giveaway_type,
    prize,
    requires_uid,
    requires_server,
    confidence,
    review_required,
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO giveaways
                (
                    video_id,
                    giveaway_type,
                    prize_description,
                    requires_uid,
                    requires_server,
                    confidence,
                    review_required,
                    reviewed
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    FALSE
                )

                ON CONFLICT (video_id)

                DO UPDATE SET

                    giveaway_type =
                        EXCLUDED.giveaway_type,

                    prize_description =
                        EXCLUDED.prize_description,

                    requires_uid =
                        EXCLUDED.requires_uid,

                    requires_server =
                        EXCLUDED.requires_server,

                    confidence =
                        EXCLUDED.confidence,

                    review_required =
                        EXCLUDED.review_required

                RETURNING id
                """,
                (
                    video_id,
                    giveaway_type,
                    prize,
                    requires_uid,
                    requires_server,
                    confidence,
                    review_required,
                )
            )

            giveaway_id = (
                cur.fetchone()[0]
            )

        conn.commit()

    return giveaway_id


# =========================================================
# CUENTAS ACTIVAS
# =========================================================

def get_active_accounts():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    account_name,
                    player_uid,
                    genshin_server

                FROM accounts

                WHERE active = TRUE

                ORDER BY id
                """
            )

            return cur.fetchall()


# =========================================================
# NÚMERO DE CUENTAS
# =========================================================

def accounts_for_type(
    giveaway_type
):

    # WELKIN:
    # 2 cuentas
    #
    # GRANDE:
    # hasta 4 cuentas
    #
    # WELKIN_Y_GRANDE:
    # hasta 4 cuentas

    if giveaway_type == "WELKIN":

        return 2

    if giveaway_type in (
        "GRANDE",
        "WELKIN_Y_GRANDE",
    ):

        return 4

    return 0


# =========================================================
# CREAR PARTICIPACIONES
# =========================================================

def create_participations(
    giveaway_id,
    giveaway_type,
):

    accounts = get_active_accounts()

    required = accounts_for_type(
        giveaway_type
    )

    selected_accounts = (
        accounts[:required]
    )

    created = 0
    existing = 0

    with get_connection() as conn:

        with conn.cursor() as cur:

            for account in selected_accounts:

                (
                    account_id,
                    account_name,
                    player_uid,
                    genshin_server,
                ) = account

                cur.execute(
                    """
                    INSERT INTO participations
                    (
                        giveaway_id,
                        account_id,
                        status
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        'PENDIENTE'
                    )

                    ON CONFLICT
                    (
                        giveaway_id,
                        account_id
                    )

                    DO NOTHING

                    RETURNING id
                    """,
                    (
                        giveaway_id,
                        account_id,
                    )
                )

                row = cur.fetchone()

                if row:

                    created += 1

                    print(
                        "  + Participación:",
                        account_name,
                        "| UID:",
                        player_uid,
                        "|",
                        genshin_server
                    )

                else:

                    existing += 1

                    print(
                        "  = Ya existía:",
                        account_name
                    )

        conn.commit()

    return (
        created,
        existing
    )


# =========================================================
# PROCESAR RESULTADOS
# =========================================================

def process_results(
    results,
    source,
):

    total_created = 0
    total_existing = 0
    total_giveaways = 0

    if not results:

        print(
            "No hay resultados para procesar."
        )

        return (
            total_giveaways,
            total_created,
            total_existing,
        )

    for index, row in enumerate(
        results,
        start=1
    ):

        (
            video_id,
            giveaway_type,
            prize,
            requires_uid,
            requires_server,
            confidence,
            review_required,
            title,
            channel_name,
        ) = row

        print()
        print("=" * 70)

        print(
            f"[{index}/{len(results)}]"
        )

        print(
            channel_name,
            "|",
            title
        )

        print(
            "Fuente:",
            source
        )

        print(
            "Tipo:",
            giveaway_type
        )

        print(
            "Premio:",
            prize
        )

        print(
            "Confianza:",
            confidence
        )

        giveaway_id = upsert_giveaway(
            video_id,
            giveaway_type,
            prize,
            requires_uid,
            requires_server,
            confidence,
            review_required,
        )

        total_giveaways += 1

        print(
            "Giveaway ID:",
            giveaway_id
        )

        created, existing = (
            create_participations(
                giveaway_id,
                giveaway_type,
            )
        )

        total_created += created
        total_existing += existing

    return (
        total_giveaways,
        total_created,
        total_existing,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print(
        "INTEGRACIÓN DE SORTEOS -> "
        "GIVEAWAYS -> PARTICIPATIONS"
    )

    print(
        "Modelo Ollama:",
        OLLAMA_MODEL
    )

    print(
        "Versión:",
        ANALYZER_VERSION
    )

    print("=" * 70)

    # =====================================================
    # 1. GAMESANDCHILL
    # =====================================================

    print()
    print("=" * 70)
    print(
        "CANALES DE CONFIANZA"
    )
    print("=" * 70)

    trusted_results = (
        get_trusted_channel_videos()
    )

    print(
        "Videos nuevos de canales "
        "de confianza:",
        len(trusted_results)
    )

    (
        trusted_giveaways,
        trusted_created,
        trusted_existing,
    ) = process_results(
        trusted_results,
        "CANAL DE CONFIANZA",
    )

    # =====================================================
    # 2. RESTO DE CANALES
    # =====================================================

    print()
    print("=" * 70)
    print(
        "SORTEOS CONFIRMADOS POR OLLAMA"
    )
    print("=" * 70)

    ollama_results = (
        get_ollama_results()
    )

    print(
        "Sorteos detectados:",
        len(ollama_results)
    )

    (
        ollama_giveaways,
        ollama_created,
        ollama_existing,
    ) = process_results(
        ollama_results,
        "OLLAMA",
    )

    # =====================================================
    # RESUMEN
    # =====================================================

    total_giveaways = (
        trusted_giveaways
        + ollama_giveaways
    )

    total_created = (
        trusted_created
        + ollama_created
    )

    total_existing = (
        trusted_existing
        + ollama_existing
    )

    print()
    print("=" * 70)
    print(
        "RESUMEN"
    )
    print("=" * 70)

    print(
        "GamesAndChill nuevos:",
        trusted_giveaways
    )

    print(
        "Sorteos confirmados "
        "por Ollama:",
        ollama_giveaways
    )

    print(
        "Sorteos sincronizados:",
        total_giveaways
    )

    print(
        "Participaciones creadas:",
        total_created
    )

    print(
        "Participaciones existentes:",
        total_existing
    )


if __name__ == "__main__":
    main()

