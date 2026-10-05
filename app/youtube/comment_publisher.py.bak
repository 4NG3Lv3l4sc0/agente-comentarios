import os
import psycopg

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# =========================================================
# CONFIGURACIÓN
# =========================================================

OAUTH_DIRECTORY = "/app/data/oauth"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl"
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
# CARGAR CREDENCIALES
# =========================================================

def get_credentials(account_number):

    token_file = os.path.join(
        OAUTH_DIRECTORY,
        f"account_{account_number}.json"
    )

    if not os.path.exists(token_file):

        raise FileNotFoundError(
            f"No existe OAuth para Cuenta {account_number}: "
            f"{token_file}"
        )

    credentials = Credentials.from_authorized_user_file(
        token_file,
        SCOPES
    )

    # Renovar access token cuando sea necesario.
    if (
        credentials.expired
        and credentials.refresh_token
    ):

        credentials.refresh(
            Request()
        )

        # Guardamos el token actualizado.
        with open(
            token_file,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                credentials.to_json()
            )

    if not credentials.valid:

        raise RuntimeError(
            f"Credenciales inválidas para Cuenta "
            f"{account_number}"
        )

    return credentials


# =========================================================
# CREAR CLIENTE YOUTUBE
# =========================================================

def get_youtube(account_number):

    credentials = get_credentials(
        account_number
    )

    return build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


# =========================================================
# OBTENER COMENTARIOS PENDIENTES
# =========================================================

def get_pending_comments():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    gc.id,
                    gc.video_id,
                    v.youtube_video_id,
                    v.title,
                    c.channel_name,
                    gc.account_number,
                    gc.template_id,
                    gc.comment_text

                FROM generated_comments gc

                JOIN videos v
                    ON v.id = gc.video_id

                JOIN channels c
                    ON c.id = v.channel_id

                WHERE gc.status = 'GENERADO'

                ORDER BY
                    v.published_at ASC,
                    gc.account_number ASC
                """
            )

            return cur.fetchall()


# =========================================================
# PUBLICAR COMENTARIO
# =========================================================

def publish_comment(
    youtube,
    youtube_video_id,
    comment_text
):

    response = (
        youtube
        .commentThreads()
        .insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": youtube_video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": comment_text
                        }
                    }
                }
            }
        )
        .execute()
    )

    # ID del hilo de comentarios.
    thread_id = response.get(
        "id"
    )

    # ID del comentario superior.
    comment_id = (
        response
        .get("snippet", {})
        .get("topLevelComment", {})
        .get("id")
    )

    # Preferimos guardar el ID real del comentario.
    return comment_id or thread_id


# =========================================================
# MARCAR COMO PUBLICADO
# =========================================================

def mark_as_published(
    generated_comment_id,
    youtube_comment_id
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            # =====================================================
            # MARCAR GENERATED_COMMENT COMO PUBLICADO
            # =====================================================

            cur.execute(
                """
                UPDATE generated_comments

                SET
                    status = 'PUBLICADO',
                    published_at = CURRENT_TIMESTAMP,
                    youtube_comment_id = %s

                WHERE id = %s
                """,
                (
                    youtube_comment_id,
                    generated_comment_id,
                )
            )

            # =====================================================
            # SINCRONIZAR PARTICIPATION
            # =====================================================

            cur.execute(
                """
                UPDATE participations p

                SET
                    status = 'PUBLICADO',
                    published_at = CURRENT_TIMESTAMP,
                    youtube_comment_id = %s,
                    comment_text = gc.comment_text,
                    error_message = NULL

                FROM giveaways g,
                     generated_comments gc,
                     accounts a

                WHERE
                    gc.id = %s

                    AND g.video_id = gc.video_id

                    AND p.giveaway_id = g.id

                    AND a.id = p.account_id

                    AND a.id = gc.account_number
                """,
                (
                    youtube_comment_id,
                    generated_comment_id,
                )
            )

        conn.commit()

# =========================================================
# MOSTRAR ERROR HTTP
# =========================================================

def show_http_error(error):

    status_code = getattr(
        error.resp,
        "status",
        "desconocido"
    )

    print(
        "ERROR YOUTUBE HTTP:",
        status_code
    )

    try:

        print(
            error.content.decode(
                "utf-8"
            )
        )

    except Exception:

        print(
            str(error)
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("PUBLICADOR DE COMENTARIOS YOUTUBE")
    print("=" * 70)

    pending = get_pending_comments()

    print(
        "Comentarios pendientes:",
        len(pending)
    )

    if not pending:

        print(
            "No hay comentarios pendientes."
        )

        return

    # Reutilizamos un cliente por cuenta.
    youtube_clients = {}

    published = 0
    failed = 0

    for index, row in enumerate(
        pending,
        start=1
    ):

        (
            generated_comment_id,
            video_id,
            youtube_video_id,
            title,
            channel_name,
            account_number,
            template_id,
            comment_text,
        ) = row

        print()
        print("=" * 70)

        print(
            f"[{index}/{len(pending)}]"
        )

        print(
            "Canal destino:",
            channel_name
        )

        print(
            "Video:",
            title
        )

        print(
            "Cuenta:",
            account_number
        )

        print(
            "Plantilla:",
            template_id
        )

        print(
            "Comentario:"
        )

        print(
            comment_text
        )

        try:

            # ---------------------------------------------
            # CLIENTE YOUTUBE
            # ---------------------------------------------

            if (
                account_number
                not in youtube_clients
            ):

                youtube_clients[
                    account_number
                ] = get_youtube(
                    account_number
                )

            youtube = youtube_clients[
                account_number
            ]

            # ---------------------------------------------
            # PUBLICAR
            # ---------------------------------------------

            youtube_comment_id = (
                publish_comment(
                    youtube,
                    youtube_video_id,
                    comment_text
                )
            )

            # ---------------------------------------------
            # SOLO MARCAR DESPUÉS DEL ÉXITO
            # ---------------------------------------------

            mark_as_published(
                generated_comment_id,
                youtube_comment_id
            )

            published += 1

            print()
            print(
                "PUBLICADO CORRECTAMENTE"
            )

            print(
                "YouTube comment ID:",
                youtube_comment_id
            )

        except HttpError as error:

            failed += 1

            print()
            show_http_error(
                error
            )

            print(
                "El registro permanece como GENERADO."
            )

        except Exception as error:

            failed += 1

            print()
            print(
                "ERROR:",
                type(error).__name__,
                str(error)
            )

            print(
                "El registro permanece como GENERADO."
            )


    # =====================================================
    # RESUMEN
    # =====================================================

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    print(
        "Publicados:",
        published
    )

    print(
        "Errores:",
        failed
    )

    print(
        "Total procesados:",
        len(pending)
    )


if __name__ == "__main__":
    main()

