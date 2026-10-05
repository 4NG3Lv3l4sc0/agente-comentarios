import os
import random
import psycopg

from templates import (
    COMMENT_TEMPLATES,
    CONTEXTUAL_TEMPLATES,
    validate_templates,
)

# =========================================================
# CONFIGURACIÓN
# =========================================================

UID = "604568115"
SERVER = "America"

COMMENTS_PER_WELKIN = 2

GENERATOR_VERSION = "1.0"


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
# OBTENER PARTICIPACIONES PENDIENTES
# =========================================================

def get_pending_participations():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    p.id AS participation_id,
                    p.giveaway_id,

                    a.id AS account_id,
                    a.account_name,
                    a.player_uid,
                    a.genshin_server,

                    g.giveaway_type,

                    v.id AS video_id,
                    v.youtube_video_id,
                    v.title,

                    c.channel_name

                FROM participations p

                JOIN accounts a
                    ON a.id = p.account_id

                JOIN giveaways g
                    ON g.id = p.giveaway_id

                JOIN videos v
                    ON v.id = g.video_id

                JOIN channels c
                    ON c.id = v.channel_id

                WHERE
                    p.status = 'PENDIENTE'
                    AND a.active = TRUE

                ORDER BY
                    v.published_at ASC,
                    a.id ASC
                """
            )

            return cur.fetchall()

# =========================================================
# PLANTILLAS YA UTILIZADAS
# =========================================================

def get_used_templates():
    """
    Obtiene las plantillas que ya se han utilizado.

    Intentaremos usar primero plantillas nuevas antes
    de comenzar a repetirlas.
    """

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT template_id
                FROM generated_comments
                ORDER BY generated_at DESC
                """
            )

            rows = cur.fetchall()

    return [
        row[0]
        for row in rows
    ]


# =========================================================
# COMPROBAR COMENTARIO EXISTENTE
# =========================================================

def comment_exists(
    video_id,
    account_number
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM generated_comments
                    WHERE
                        video_id = %s
                        AND account_number = %s
                )
                """,
                (
                    video_id,
                    account_number,
                )
            )

            return cur.fetchone()[0]


# =========================================================
# DETECCIÓN DE CONTEXTO DEL VIDEO
# =========================================================

def detect_topics(title):
    """
    Detecta los temas presentes en el título utilizando
    las palabras clave definidas en CONTEXTUAL_TEMPLATES.

    Puede devolver más de un tema.
    """

    title_lower = title.lower()

    detected = []

    for topic, data in CONTEXTUAL_TEMPLATES.items():

        for keyword in data["keywords"]:

            if keyword.lower() in title_lower:

                detected.append(topic)

                break

    return detected


# =========================================================
# OBTENER PLANTILLAS CONTEXTUALES
# =========================================================

def get_contextual_candidates(
    title,
    used_templates,
    reserved_templates
):
    """
    Obtiene plantillas correspondientes a los temas
    detectados en el título.
    """

    topics = detect_topics(title)

    candidates = []

    for topic in topics:

        data = CONTEXTUAL_TEMPLATES[
            topic
        ]

        for template in data["templates"]:

            if (
                template["id"]
                not in used_templates

                and template["id"]
                not in reserved_templates
            ):

                candidate = {
                    "id": template["id"],
                    "style": f"contextual:{topic}",
                    "text": template["text"],
                }

                candidates.append(
                    candidate
                )

    return topics, candidates


# =========================================================
# SELECCIÓN DE PLANTILLA
# =========================================================

def select_template(
    title,
    used_templates,
    reserved_templates
):
    """
    Prioridad:

    1. Plantilla contextual nueva.
    2. Plantilla general nueva.
    3. Plantilla contextual reutilizable.
    4. Plantilla general reutilizable.
    """

    # =====================================================
    # 1. BUSCAR CONTEXTO
    # =====================================================

    topics, contextual = (
        get_contextual_candidates(
            title,
            used_templates,
            reserved_templates
        )
    )

    if contextual:

        return (
            random.choice(contextual),
            topics
        )


    # =====================================================
    # 2. PLANTILLAS GENERALES NO UTILIZADAS
    # =====================================================

    available = [
        template
        for template in COMMENT_TEMPLATES
        if (
            template["id"]
            not in used_templates

            and template["id"]
            not in reserved_templates
        )
    ]

    if available:

        return (
            random.choice(available),
            topics
        )


    # =====================================================
    # 3. CONTEXTUALES REUTILIZABLES
    # =====================================================

    contextual_reusable = []

    for topic in topics:

        for template in (
            CONTEXTUAL_TEMPLATES[
                topic
            ]["templates"]
        ):

            if (
                template["id"]
                not in reserved_templates
            ):

                contextual_reusable.append(
                    {
                        "id": template["id"],
                        "style":
                            f"contextual:{topic}",
                        "text": template["text"],
                    }
                )

    if contextual_reusable:

        return (
            random.choice(
                contextual_reusable
            ),
            topics
        )


    # =====================================================
    # 4. PLANTILLAS GENERALES REUTILIZABLES
    # =====================================================

    available = [
        template
        for template in COMMENT_TEMPLATES
        if (
            template["id"]
            not in reserved_templates
        )
    ]

    if not available:

        available = (
            COMMENT_TEMPLATES
        )

    return (
        random.choice(available),
        topics
    )

# =========================================================
# GENERAR TEXTO
# =========================================================

def render_comment(template):

    return template["text"].format(
        uid=UID,
        server=SERVER,
    )


# =========================================================
# GUARDAR COMENTARIO
# =========================================================

def save_comment(
    video_id,
    account_number,
    template_id,
    comment_text
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO generated_comments (
                    video_id,
                    account_number,
                    template_id,
                    comment_text,
                    status,
                    generated_at
                )

                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    'GENERADO',
                    CURRENT_TIMESTAMP
                )

                ON CONFLICT (
                    video_id,
                    account_number
                )

                DO NOTHING
                """,

                (
                    video_id,
                    account_number,
                    template_id,
                    comment_text,
                )
            )

            inserted = (
                cur.rowcount > 0
            )

        conn.commit()

    return inserted


# =========================================================
# GENERAR PARA UN VIDEO
# =========================================================

def generate_for_video(
    video,
    used_templates,
    reserved_templates
):

    (
        video_id,
        youtube_video_id,
        title,
        channel_name,
        welkin_score,
    ) = video

    print()
    print("=" * 70)

    print(
        channel_name,
        "|",
        title
    )

    print(
        "YouTube ID:",
        youtube_video_id
    )

    print(
        "Welkin score:",
        welkin_score
    )

    generated_count = 0

    for account_number in range(
        1,
        COMMENTS_PER_WELKIN + 1
    ):

        print()
        print(
            f"Cuenta {account_number}:"
        )

        if comment_exists(
            video_id,
            account_number
        ):

            print(
                "Ya existe comentario generado."
            )

            continue

        template, topics = select_template(
            title,
            used_templates,
            reserved_templates
        )

        comment_text = render_comment(
            template
        )

        inserted = save_comment(
            video_id,
            account_number,
            template["id"],
            comment_text
        )

        if not inserted:

            print(
                "No se insertó: ya existía."
            )

            continue

        reserved_templates.add(
            template["id"]
        )

        used_templates.append(
            template["id"]
        )

        generated_count += 1

        if topics:

            print(
                "Temas detectados:",
                ", ".join(topics)
            )

        else:

            print(
                "Temas detectados: ninguno"
            )

        print(
            "Plantilla:",
            template["id"]
        )

        print(
            "Estilo:",
            template["style"]
        )

        print(
            "Comentario:"
        )

        print(
            comment_text
        )

    return generated_count

# =========================================================
# GUARDAR COMENTARIO GENERADO
# =========================================================

def save_generated_comment(
    video_id,
    account_number,
    template_id,
    comment_text,
    participation_id,
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO generated_comments
                (
                    video_id,
                    account_number,
                    template_id,
                    comment_text,
                    status
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    'GENERADO'
                )

                ON CONFLICT
                (
                    video_id,
                    account_number
                )

                DO NOTHING

                RETURNING id
                """,
                (
                    video_id,
                    account_number,
                    template_id,
                    comment_text,
                )
            )

            row = cur.fetchone()

            if row:

                generated_comment_id = row[0]

                cur.execute(
                    """
                    UPDATE participations

                    SET
                        comment_text = %s

                    WHERE id = %s
                    """,
                    (
                        comment_text,
                        participation_id,
                    )
                )

                conn.commit()

                return generated_comment_id

        conn.commit()

    return None

# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("GENERADOR DE COMENTARIOS")
    print("Fuente: participations PENDIENTE")
    print("=" * 70)

    validate_templates()

    pending = get_pending_participations()

    print(
        "Participaciones pendientes:",
        len(pending)
    )

    if not pending:

        print(
            "No hay participaciones pendientes."
        )

        return

    used_templates = get_used_templates()

    reserved_templates = set()

    generated = 0
    existing = 0

    for index, row in enumerate(
        pending,
        start=1
    ):

        (
            participation_id,
            giveaway_id,
            account_id,
            account_name,
            player_uid,
            genshin_server,
            giveaway_type,
            video_id,
            youtube_video_id,
            title,
            channel_name,
        ) = row

        print()
        print("=" * 70)

        print(
            f"[{index}/{len(pending)}]"
        )

        print(
            channel_name,
            "|",
            title
        )

        print(
            "Tipo:",
            giveaway_type
        )

        print(
            "Cuenta:",
            account_name
        )

        print(
            "UID:",
            player_uid
        )

        print(
            "Servidor:",
            genshin_server
        )

        # -------------------------------------------------
        # IMPORTANTE
        #
        # account_id 1 -> account_1.json
        # account_id 2 -> account_2.json
        # -------------------------------------------------

        account_number = account_id

        if comment_exists(
            video_id,
            account_number
        ):

            print(
                "YA EXISTE comentario para esta cuenta."
            )

            existing += 1

            continue

        template, topics = select_template(
            title,
            used_templates,
            reserved_templates
        )

        template_id = template["id"]

        comment_text = (
            template["text"]
            .format(
                uid=player_uid,
                server=genshin_server,
            )
        )

        generated_comment_id = (
            save_generated_comment(
                video_id,
                account_number,
                template_id,
                comment_text,
                participation_id,
            )
        )

        if generated_comment_id:

            generated += 1

            reserved_templates.add(
                template_id
            )

            used_templates.append(
                template_id
            )

            print(
                "Plantilla:",
                template_id
            )

            if topics:

                print(
                    "Contexto:",
                    ", ".join(topics)
                )

            else:

                print(
                    "Contexto: general"
                )

            print()
            print(
                "COMENTARIO GENERADO:"
            )

            print(
                comment_text
            )

            print(
                "Generated comment ID:",
                generated_comment_id
            )

        else:

            existing += 1

            print(
                "El comentario ya existía."
            )

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    print(
        "Participaciones encontradas:",
        len(pending)
    )

    print(
        "Comentarios generados:",
        generated
    )

    print(
        "Comentarios existentes:",
        existing
    )

if __name__ == "__main__":
    main()

