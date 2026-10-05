import os
import sys
import time
import subprocess
from datetime import datetime


# =========================================================
# CONFIGURACIÓN
# =========================================================

BASE_DIR = "/app"
PYTHON = sys.executable

LINE = "=" * 74


# =========================================================
# PIPELINE
# =========================================================
#
# required:
#   True  -> si falla, se detiene el agente.
#   False -> si no existe, se omite.
#
# alternatives:
#   permite buscar distintos nombres usados durante
#   las versiones anteriores del proyecto.
# =========================================================

PIPELINE = [

    {
        "name": "Buscar videos nuevos",
        "alternatives": [
            "app/youtube/videos.py",
        ],
        "required": True,
    },

    {
        "name": "Obtener transcripciones",
        "alternatives": [
            "app/youtube/transcripts.py",
        ],
        "required": True,
    },

    {
        "name": "Analizar transcripciones",
        "alternatives": [
            "app/detector/transcript_detector.py",
        ],
        "required": True,
    },

    {
        "name": "Analizar comentarios",
        "alternatives": [
            "app/detector/comments.py",
        ],
        "required": True,
    },

    {
        "name": "Clasificar tipo de sorteo",
        "alternatives": [
            "app/detector/giveaway_type_classifier.py",
        ],
        "required": False,
    },

    {
        "name": "Detectar Welkin",
        "alternatives": [
            "app/detector/welkin_detector.py",
        ],
        "required": False,
    },

    {
        "name": "Clasificación global",
        "alternatives": [
            "app/detector/global_classifier.py",
        ],
        "required": True,
    },

    {
        "name": "Análisis IA local con Ollama",
        "alternatives": [
            "app/ai/ollama_giveaway_analyzer_v2.py",
        ],
        "required": True,
    },

    {
        "name": "Integrar sorteos y participaciones",
        "alternatives": [
            "app/ai/integrate_ollama_results.py",
        ],
        "required": True,
    },

    {
        "name": "Generar comentarios",
        "alternatives": [
            "app/comments/generator.py",
        ],
        "required": True,
    },

    {
        "name": "Publicar comentarios",
        "alternatives": [
            "app/youtube/comment_publisher.py",
        ],
        "required": True,
    },
]

# =========================================================
# BUSCAR SCRIPT
# =========================================================

def find_script(alternatives):

    for relative_path in alternatives:

        full_path = os.path.join(
            BASE_DIR,
            relative_path,
        )

        if os.path.isfile(full_path):
            return relative_path

    return None


# =========================================================
# EJECUTAR SCRIPT
# =========================================================

def run_script(name, relative_path):

    full_path = os.path.join(
        BASE_DIR,
        relative_path,
    )

    print()
    print(LINE)
    print(name)
    print(LINE)
    print("Script:", relative_path)
    print()

    start = time.time()

    try:

        process = subprocess.run(
            [
                PYTHON,
                "-u",
                full_path,
            ],
            cwd=BASE_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        elapsed = time.time() - start

        output = process.stdout or ""

        if output:

            print(
                output,
                end=""
                if output.endswith("\n")
                else "\n",
            )

        return {
            "ok": process.returncode == 0,
            "returncode": process.returncode,
            "elapsed": elapsed,
            "script": relative_path,
            "error": None,
        }

    except Exception as exc:

        elapsed = time.time() - start

        return {
            "ok": False,
            "returncode": -1,
            "elapsed": elapsed,
            "script": relative_path,
            "error": str(exc),
        }


# =========================================================
# MOSTRAR PIPELINE DETECTADO
# =========================================================

def show_pipeline():

    print()
    print(LINE)
    print("PIPELINE DETECTADO")
    print(LINE)

    detected = []

    for index, step in enumerate(
        PIPELINE,
        start=1,
    ):

        script = find_script(
            step["alternatives"]
        )

        if script:

            print(
                f"{index:02d}. OK     "
                f"{step['name']}"
            )

            print(
                f"          {script}"
            )

        else:

            status = (
                "FALTA"
                if step["required"]
                else "OMITIR"
            )

            print(
                f"{index:02d}. {status:6} "
                f"{step['name']}"
            )

        detected.append(
            (
                step,
                script,
            )
        )

    return detected


# =========================================================
# MAIN
# =========================================================

def main():

    total_start = time.time()

    print(LINE)
    print("GENSHIN GIVEAWAY AGENT")
    print("PIPELINE AUTOMÁTICO")
    print(LINE)

    print(
        "Inicio:",
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(
        "Python:",
        sys.version.split()[0]
    )

    print(
        "Directorio:",
        BASE_DIR
    )

    detected = show_pipeline()

    # =====================================================
    # VALIDAR REQUERIDOS ANTES DE HACER NADA
    # =====================================================

    missing_required = []

    for step, script in detected:

        if (
            step["required"]
            and script is None
        ):

            missing_required.append(
                step["name"]
            )

    if missing_required:

        print()
        print(LINE)
        print("PIPELINE INCOMPLETO")
        print(LINE)

        for name in missing_required:

            print(
                "Falta:",
                name
            )

        print()
        print(
            "No se inició el ciclo."
        )

        sys.exit(1)

    # =====================================================
    # EJECUCIÓN
    # =====================================================

    results = []

    executable_steps = [
        (step, script)
        for step, script in detected
        if script is not None
    ]

    total_steps = len(
        executable_steps
    )

    for number, (
        step,
        script,
    ) in enumerate(
        executable_steps,
        start=1,
    ):

        print()
        print(
            f">>> PASO "
            f"{number}/{total_steps}"
        )

        result = run_script(
            step["name"],
            script,
        )

        result[
            "name"
        ] = step["name"]

        results.append(
            result
        )

        if result["ok"]:

            print()
            print(
                "RESULTADO: OK"
            )

            print(
                "Tiempo:",
                f"{result['elapsed']:.2f}s"
            )

        else:

            print()
            print(
                "RESULTADO: ERROR"
            )

            print(
                "Código:",
                result["returncode"]
            )

            if result["error"]:

                print(
                    "Detalle:",
                    result["error"]
                )

            if step["required"]:

                print()
                print(
                    "Paso obligatorio falló."
                )

                print(
                    "Se detiene el pipeline "
                    "para evitar publicaciones "
                    "con datos incompletos."
                )

                break

            else:

                print()
                print(
                    "Paso opcional falló. "
                    "El pipeline continúa."
                )

    # =====================================================
    # RESUMEN
    # =====================================================

    total_elapsed = (
        time.time() - total_start
    )

    print()
    print(LINE)
    print("RESUMEN DEL CICLO")
    print(LINE)

    successful = 0
    failed = 0

    for result in results:

        if result["ok"]:

            successful += 1
            status = "OK"

        else:

            failed += 1
            status = "ERROR"

        print(
            f"{status:5} | "
            f"{result['name']:<38} | "
            f"{result['elapsed']:>7.2f}s"
        )

    print()
    print(
        "Pasos ejecutados:",
        len(results)
    )

    print(
        "Correctos:",
        successful
    )

    print(
        "Errores:",
        failed
    )

    print(
        "Tiempo total:",
        f"{total_elapsed:.2f}s"
    )

    print(
        "Fin:",
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(LINE)

    if failed:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
PY
