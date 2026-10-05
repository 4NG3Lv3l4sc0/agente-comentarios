import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# =========================================================
# CONFIGURACIÓN
# =========================================================

CLIENT_SECRET_FILE = (
    "/app/data/oauth/client_secret.json"
)

OAUTH_DIRECTORY = (
    "/app/data/oauth"
)

# Este permiso nos permitirá posteriormente trabajar
# con comentarios de YouTube.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl"
]


# =========================================================
# AUTORIZACIÓN
# =========================================================

def authorize_account(account_number):

    token_file = os.path.join(
        OAUTH_DIRECTORY,
        f"account_{account_number}.json"
    )

    print("=" * 70)
    print("CONFIGURACIÓN OAUTH DE YOUTUBE")
    print("=" * 70)

    print(
        f"Cuenta interna: {account_number}"
    )

    print(
        "Archivo de token:",
        token_file
    )

    print()

    if os.path.exists(token_file):

        print(
            "ERROR: ya existe un token para "
            f"la cuenta {account_number}."
        )

        print(
            "No se sobrescribirá automáticamente."
        )

        return


    # =====================================================
    # CREAR FLUJO OAUTH
    # =====================================================

    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        SCOPES
    )


    # =====================================================
    # AUTORIZAR
    # =====================================================

    print(
        "Se iniciará la autorización de Google."
    )

    print()
    print(
        "IMPORTANTE:"
    )

    print(
        "Selecciona la cuenta de Google que "
        f"quieres asociar con Cuenta {account_number}."
    )

    print()

    credentials = flow.run_local_server(
        host="127.0.0.1",
        bind_addr="0.0.0.0",
        port=8085,
        open_browser=False,
        authorization_prompt_message=(
            "\nAbre esta URL en tu navegador:\n\n{url}\n"
        ),
        success_message=(
            "Autorización completada. "
            "Puedes cerrar esta ventana."
        ),
    )


    # =====================================================
    # CONECTAR CON YOUTUBE
    # =====================================================

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials
    )


    # =====================================================
    # IDENTIFICAR CANAL
    # =====================================================

    response = (
        youtube
        .channels()
        .list(
            part="snippet",
            mine=True
        )
        .execute()
    )

    items = response.get(
        "items",
        []
    )

    if not items:

        print()
        print(
            "ERROR: la cuenta autorizada no "
            "tiene un canal de YouTube disponible."
        )

        return

    channel = items[0]

    channel_id = channel["id"]

    channel_title = (
        channel["snippet"]["title"]
    )


    # =====================================================
    # MOSTRAR ANTES DE GUARDAR
    # =====================================================

    print()
    print("=" * 70)

    print(
        "AUTORIZACIÓN CORRECTA"
    )

    print("=" * 70)

    print(
        "Cuenta interna:",
        account_number
    )

    print(
        "Canal:",
        channel_title
    )

    print(
        "Channel ID:",
        channel_id
    )

    print()

    print(
        "Publicación de comentarios: DESACTIVADA"
    )


    # =====================================================
    # GUARDAR TOKEN
    # =====================================================

    with open(
        token_file,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            credentials.to_json()
        )

    print()
    print(
        "Token OAuth guardado correctamente."
    )

    print(
        "Archivo:",
        token_file
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if len(sys.argv) != 2:

        print(
            "Uso:"
        )

        print(
            "python app/youtube/oauth_setup.py "
            "<numero_cuenta>"
        )

        print()
        print(
            "Ejemplo:"
        )

        print(
            "python app/youtube/oauth_setup.py 1"
        )

        return

    try:

        account_number = int(
            sys.argv[1]
        )

    except ValueError:

        print(
            "El número de cuenta debe ser entero."
        )

        return

    if account_number < 1:

        print(
            "El número de cuenta debe ser mayor que 0."
        )

        return

    authorize_account(
        account_number
    )


if __name__ == "__main__":
    main()

