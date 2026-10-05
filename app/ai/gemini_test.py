import os

from google import genai


# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY no está configurada."
    )


# =========================================================
# CLIENTE GEMINI
# =========================================================

client = genai.Client(
    api_key=API_KEY
)


# =========================================================
# PRUEBA
# =========================================================

print("=" * 70)
print("PRUEBA DE CONEXIÓN CON GEMINI")
print("=" * 70)

try:

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=(
            "Responde únicamente con la palabra OK. "
            "No agregues ningún otro texto."
        ),
    )

    print(
        "Respuesta:",
        response.text
    )

    print()
    print(
        "GEMINI_API: CONEXIÓN CORRECTA"
    )

except Exception as error:

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

