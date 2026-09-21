from flask import Flask, jsonify, request, render_template_string
import os
import time
import threading

app = Flask(__name__)


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN_MAIN = os.environ.get("TOKEN_MAIN", "")

NUM_SENSORES = 2

# Límite provisional de alerta
LIMITE_ALERTA = 150

# 45 minutos
INTERVALO_PROMEDIO = 45 * 60

# Si el servidor no recibe datos durante 15 segundos,
# el sistema se mostrará como desconectado.
TIMEOUT_MAIN = 15

lock = threading.Lock()

inicio_periodo = time.time()

ultima_conexion_main = 0


# ============================================================
# DATOS DE LOS SENSORES
# ============================================================

sensores = [
    {
        "id": 1,
        "nombre": "Muestra 1",
        "valor": 0,
        "suma": 0,
        "muestras": 0,
        "promedio": 0.0,
        "alerta": False,
        "conectado": False
    },
    {
        "id": 2,
        "nombre": "Muestra 2",
        "valor": 0,
        "suma": 0,
        "muestras": 0,
        "promedio": 0.0,
        "alerta": False,
        "conectado": False
    }
]


# ============================================================
# HISTORIAL
# ============================================================

historial = []


# ============================================================
# EVITAR CACHE
# ============================================================

@app.after_request
def no_cache(response):

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


# ============================================================
# COMPROBAR Y CERRAR PERÍODO DE 45 MINUTOS
# ============================================================

def comprobar_periodo():

    global inicio_periodo

    ahora = time.time()

    if ahora - inicio_periodo < INTERVALO_PROMEDIO:
        return

    for sensor in sensores:

        if sensor["muestras"] > 0:

            promedio_final = (
                sensor["suma"]
                /
                sensor["muestras"]
            )

            historial.insert(
                0,
                {
                    "id": sensor["id"],
                    "nombre": sensor["nombre"],
                    "promedio": round(promedio_final, 1),
                    "alerta": promedio_final >= LIMITE_ALERTA,
                    "periodo": "45 min",
                    "timestamp": int(ahora)
                }
            )

        # Reiniciar acumuladores
        sensor["suma"] = 0
        sensor["muestras"] = 0
        sensor["promedio"] = 0.0

    # Mantener máximo 100 registros
    if len(historial) > 100:
        del historial[100:]

    inicio_periodo = time.time()


# ============================================================
# RECIBIR DATOS DESDE EL ESP32 PRINCIPAL
#
# POST /api/main
#
# JSON esperado:
#
# {
#   "sensores": [
#       {
#           "id": 1,
#           "valor": 120,
#           "alerta": false,
#           "conectado": true
#       },
#       {
#           "id": 2,
#           "valor": 170,
#           "alerta": true,
#           "conectado": true
#       }
#   ]
# }
# ============================================================

@app.route("/api/main", methods=["POST"])
def recibir_main():

    global ultima_conexion_main

    # ========================================================
    # COMPROBAR TOKEN
    # ========================================================

    token = request.headers.get("X-Token", "")

    if not TOKEN_MAIN or token != TOKEN_MAIN:

        return jsonify({
            "ok": False,
            "error": "No autorizado"
        }), 401


    # ========================================================
    # LEER JSON
    # ========================================================

    datos = request.get_json(silent=True)

    if not isinstance(datos, dict):

        return jsonify({
            "ok": False,
            "error": "JSON invalido"
        }), 400


    recibidos = datos.get("sensores")

    if not isinstance(recibidos, list):

        return jsonify({
            "ok": False,
            "error": "Lista de sensores invalida"
        }), 400


    # ========================================================
    # ACTUALIZAR DATOS
    # ========================================================

    with lock:

        ultima_conexion_main = time.time()

        comprobar_periodo()

        for recibido in recibidos:

            if not isinstance(recibido, dict):
                continue

            try:

                sensor_id = int(
                    recibido.get("id", 0)
                )

                valor = int(
                    recibido.get("valor", 0)
                )

            except (TypeError, ValueError):
                continue


            if sensor_id not in (1, 2):
                continue


            indice = sensor_id - 1

            sensor = sensores[indice]


            conectado = bool(
                recibido.get(
                    "conectado",
                    False
                )
            )


            sensor["conectado"] = conectado


            if conectado:

                sensor["valor"] = valor

                # El servidor calcula el estado.
                sensor["alerta"] = (
                    valor >= LIMITE_ALERTA
                )

                # Acumular lectura
                sensor["suma"] += valor

                sensor["muestras"] += 1

                # Promedio acumulado actual
                sensor["promedio"] = (
                    sensor["suma"]
                    /
                    sensor["muestras"]
                )


    return jsonify({
        "ok": True
    }), 200


# ============================================================
# API DE DATOS EN VIVO
# ============================================================

@app.route("/api/datos")
def api_datos():

    ahora = time.time()

    with lock:

        comprobar_periodo()

        main_conectado = (
            ultima_conexion_main > 0
            and
            ahora - ultima_conexion_main <= TIMEOUT_MAIN
        )


        restante = max(
            0,
            INTERVALO_PROMEDIO
            -
            int(
                ahora - inicio_periodo
            )
        )


        respuesta_sensores = []


        for sensor in sensores:

            conectado_real = (
                main_conectado
                and
                sensor["conectado"]
            )


            respuesta_sensores.append({
                "id": sensor["id"],
                "nombre": sensor["nombre"],
                "valor": sensor["valor"],
                "promedio": round(
                    sensor["promedio"],
                    1
                ),
                "alerta": sensor["alerta"],
                "conectado": conectado_real
            })


    return jsonify({
        "conectado": main_conectado,
        "tiempo_restante": restante,
        "intervalo_promedio": INTERVALO_PROMEDIO,
        "sensores": respuesta_sensores
    })


# ============================================================
# API DE PROMEDIOS
# ============================================================

@app.route("/api/promedios")
def api_promedios():

    with lock:

        comprobar_periodo()

        return jsonify({
            "historial": historial
        })


# ============================================================
# API DE ESTADO
# ============================================================

@app.route("/api/status")
def api_status():

    ahora = time.time()

    with lock:

        conectado = (
            ultima_conexion_main > 0
            and
            ahora - ultima_conexion_main <= TIMEOUT_MAIN
        )


    return jsonify({
        "servidor": True,
        "conectado": conectado,
        "sensores": NUM_SENSORES,
        "limite_alerta": LIMITE_ALERTA
    })


# ============================================================
# INTERFAZ WEB
# ============================================================

HTML = r"""
<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Monitoreo portable de CO₂</title>


<style>

/* ==========================================================
   GENERAL
   ========================================================== */

* {
    box-sizing: border-box;
}


body {

    margin: 0;

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background: #f5f6f8;

    color: #18181b;
}


/* ==========================================================
   SIDEBAR
   ========================================================== */

.sidebar {

    position: fixed;

    top: 18px;
    left: 18px;

    width: 68px;

    padding: 9px;

    display: flex;

    flex-direction: column;

    gap: 8px;

    background: #ffffff;

    border: 1px solid #e4e4e7;

    border-radius: 20px;

    box-shadow:
        0 8px 30px
        rgba(0, 0, 0, .06);

    z-index: 10;
}


.nav-button {

    width: 48px;
    height: 48px;

    border: none;

    border-radius: 14px;

    background: transparent;

    font-size: 20px;

    cursor: pointer;

    transition:
        background .18s ease,
        transform .18s ease;
}


.nav-button:hover {

    background: #f4f4f5;

    transform: translateY(-1px);
}


.nav-button.active {

    background: #000080;

    color: #ffffff;
}


/* ==========================================================
   CONTENEDOR
   ========================================================== */

.container {

    width:
        min(
            1050px,
            calc(100% - 130px)
        );

    margin: 0 auto;

    padding: 50px 0 80px;
}


/* ==========================================================
   HEADER
   ========================================================== */

.header {

    margin-bottom: 34px;
}


.eyebrow {

    color: #000080;

    font-size: 12px;

    font-weight: 800;

    letter-spacing: 1.5px;

    text-transform: uppercase;

    margin-bottom: 8px;
}


.header h1 {

    margin: 0;

    font-size:
        clamp(
            30px,
            4vw,
            44px
        );

    letter-spacing: -1.7px;
}


.subtitle {

    margin-top: 8px;

    color: #71717a;

    font-size: 15px;
}


/* ==========================================================
   ESTADO GENERAL
   ========================================================== */

.status-row {

    display: flex;

    margin-top: 22px;
}


.status-pill {

    display: flex;

    align-items: center;

    gap: 8px;

    padding: 9px 13px;

    background: #ffffff;

    border: 1px solid #e4e4e7;

    border-radius: 999px;

    font-size: 13px;
}


.dot {

    width: 8px;
    height: 8px;

    border-radius: 50%;

    background: #a1a1aa;
}


.dot.ok {

    background: #22c55e;
}


.dot.off {

    background: #ef4444;
}


/* ==========================================================
   GRID
   ========================================================== */

.grid {

    display: grid;

    grid-template-columns:
        repeat(
            2,
            minmax(0, 1fr)
        );

    gap: 20px;
}


/* ==========================================================
   TARJETAS
   ========================================================== */

.card {

    min-height: 370px;

    padding: 25px;

    background: #ffffff;

    border: 1px solid #e4e4e7;

    border-radius: 26px;

    box-shadow:
        0 8px 35px
        rgba(0, 0, 0, .04);

    transition:
        transform .22s ease,
        box-shadow .22s ease;
}


.card:hover {

    transform: translateY(-3px);

    box-shadow:
        0 14px 45px
        rgba(0, 0, 0, .07);
}


.card-top {

    display: flex;

    justify-content: space-between;

    align-items: center;

    gap: 15px;
}


.sensor-name {

    font-size: 15px;

    font-weight: 750;
}


.connection {

    color: #71717a;

    font-size: 12px;
}


/* ==========================================================
   VALOR
   ========================================================== */

.value {

    margin-top: 25px;

    font-size:
        clamp(
            58px,
            8vw,
            82px
        );

    font-weight: 750;

    letter-spacing: -4px;

    line-height: 1;
}


.value-label {

    margin-top: 8px;

    color: #a1a1aa;

    font-size: 12px;
}


/* ==========================================================
   ESTADOS
   ========================================================== */

.state {

    display: inline-block;

    margin-top: 20px;

    padding: 7px 11px;

    border-radius: 999px;

    font-size: 11px;

    font-weight: 800;

    letter-spacing: .7px;
}


.state.normal {

    color: #166534;

    background: #dcfce7;
}


.state.alert {

    color: #991b1b;

    background: #fee2e2;
}


.state.offline {

    color: #71717a;

    background: #f4f4f5;
}


/* ==========================================================
   DETALLES
   ========================================================== */

.details {

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 18px;

    margin-top: 27px;

    padding-top: 20px;

    border-top: 1px solid #f0f0f0;
}


.detail-title {

    margin-bottom: 6px;

    color: #a1a1aa;

    font-size: 11px;
}


.detail-value {

    font-size: 15px;

    font-weight: 700;
}


/* ==========================================================
   PROGRESO
   ========================================================== */

.progress {

    height: 6px;

    margin-top: 20px;

    overflow: hidden;

    background: #f1f1f1;

    border-radius: 100px;
}


.progress-bar {

    width: 0%;

    height: 100%;

    background: #000080;

    border-radius: 100px;

    transition: width .7s ease;
}


/* ==========================================================
   HISTORIAL
   ========================================================== */

.history {

    display: none;
}


.history.active {

    display: block;
}


.live.hidden {

    display: none;
}


.history-card {

    overflow: hidden;

    background: #ffffff;

    border: 1px solid #e4e4e7;

    border-radius: 26px;
}


.history-header {

    padding: 24px;

    border-bottom: 1px solid #eeeeee;
}


.history-header h2 {

    margin: 0;

    font-size: 21px;
}


.history-header p {

    margin: 7px 0 0;

    color: #71717a;

    font-size: 13px;
}


table {

    width: 100%;

    border-collapse: collapse;
}


th,
td {

    padding: 17px 22px;

    text-align: left;

    border-bottom: 1px solid #f1f1f1;

    font-size: 13px;
}


th {

    color: #71717a;

    font-size: 11px;

    text-transform: uppercase;
}


/* ==========================================================
   RESPONSIVE
   ========================================================== */

@media(max-width: 750px) {

    .sidebar {

        position: static;

        width: max-content;

        margin: 15px auto 0;

        flex-direction: row;
    }


    .container {

        width: calc(100% - 28px);

        padding-top: 30px;
    }


    .grid {

        grid-template-columns: 1fr;
    }
}

</style>

</head>


<body>


<!-- ========================================================
     NAVEGACIÓN
     ======================================================== -->

<div class="sidebar">

    <button
        id="btnLive"
        class="nav-button active"
        onclick="mostrarLive()"
        title="En vivo"
    >
        ●
    </button>


    <button
        id="btnHistory"
        class="nav-button"
        onclick="mostrarHistorial()"
        title="Promedios"
    >
        ≡
    </button>

</div>


<div class="container">


<!-- ========================================================
     HEADER
     ======================================================== -->

<header class="header">

    <div class="eyebrow">
        MONITOREO PORTABLE DE CO₂
    </div>


    <h1>
        Monitoreo portable de CO₂
    </h1>


    <div class="subtitle">
        Monitoreo en tiempo real · 2 muestras
    </div>


    <div class="status-row">

        <div class="status-pill">

            <span
                id="statusDot"
                class="dot"
            >
            </span>


            <span id="statusText">
                Comprobando...
            </span>

        </div>

    </div>

</header>


<!-- ========================================================
     EN VIVO
     ======================================================== -->

<section
    id="liveView"
    class="live"
>

    <div
        id="sensorGrid"
        class="grid"
    >
    </div>

</section>


<!-- ========================================================
     HISTORIAL
     ======================================================== -->

<section
    id="historyView"
    class="history"
>

    <div class="history-card">


        <div class="history-header">

            <h2>
                Promedios
            </h2>

            <p>
                Períodos completados de 45 minutos
            </p>

        </div>


        <div style="overflow-x:auto">

            <table>

                <thead>

                    <tr>

                        <th>
                            Muestra
                        </th>

                        <th>
                            Promedio
                        </th>

                        <th>
                            Estado
                        </th>

                        <th>
                            Período
                        </th>

                    </tr>

                </thead>


                <tbody id="historyBody">
                </tbody>

            </table>

        </div>

    </div>

</section>


</div>


<script>

/* ==========================================================
   CREAR TARJETAS
   ========================================================== */

function crearTarjetas() {

    const grid =
        document.getElementById(
            "sensorGrid"
        );


    grid.innerHTML = "";


    for (
        let id = 1;
        id <= 2;
        id++
    ) {

        grid.innerHTML += `

        <article class="card">

            <div class="card-top">

                <div
                    class="sensor-name"
                    id="name-${id}"
                >
                    Muestra ${id}
                </div>


                <div
                    class="connection"
                    id="connection-${id}"
                >
                    Esperando...
                </div>

            </div>


            <div
                class="value"
                id="value-${id}"
            >
                --
            </div>


            <div class="value-label">
                Nivel detectado
            </div>


            <div
                class="state offline"
                id="state-${id}"
            >
                DESCONECTADO
            </div>


            <div class="details">

                <div>

                    <div class="detail-title">
                        Promedio actual
                    </div>

                    <div
                        class="detail-value"
                        id="average-${id}"
                    >
                        --
                    </div>

                </div>


                <div>

                    <div class="detail-title">
                        Próximo promedio
                    </div>

                    <div
                        class="detail-value"
                        id="countdown-${id}"
                    >
                        --:--
                    </div>

                </div>

            </div>


            <div class="progress">

                <div
                    class="progress-bar"
                    id="progress-${id}"
                >
                </div>

            </div>

        </article>

        `;
    }
}


/* ==========================================================
   FORMATO DE TIEMPO
   ========================================================== */

function formatoTiempo(segundos) {

    segundos =
        Math.max(
            0,
            Math.floor(segundos)
        );


    const minutos =
        Math.floor(
            segundos / 60
        );


    const segundosRestantes =
        segundos % 60;


    return (
        String(minutos)
            .padStart(2, "0")
        +
        ":"
        +
        String(segundosRestantes)
            .padStart(2, "0")
    );
}


/* ==========================================================
   ACTUALIZAR PÁGINA
   ========================================================== */

async function actualizar() {

    const statusDot =
        document.getElementById(
            "statusDot"
        );


    const statusText =
        document.getElementById(
            "statusText"
        );


    try {

        const respuesta =
            await fetch(
                "/api/datos?t="
                +
                Date.now(),
                {
                    cache: "no-store"
                }
            );


        if (!respuesta.ok) {

            throw new Error(
                "Servidor no disponible"
            );
        }


        const datos =
            await respuesta.json();


        // ===================================================
        // ESTADO GENERAL
        // ===================================================

        if (datos.conectado) {

            statusDot.className =
                "dot ok";

            statusText.textContent =
                "Conectado";

        } else {

            statusDot.className =
                "dot off";

            statusText.textContent =
                "Desconectado";
        }


        // ===================================================
        // PROGRESO DEL PROMEDIO
        // ===================================================

        const restante =
            Number(
                datos.tiempo_restante
            );


        const intervalo =
            Number(
                datos.intervalo_promedio
            );


        const porcentaje =
            intervalo > 0
            ?
            (
                (
                    intervalo
                    -
                    restante
                )
                /
                intervalo
            )
            *
            100
            :
            0;


        // ===================================================
        // ACTUALIZAR SENSORES
        // ===================================================

        for (
            const sensor
            of datos.sensores
        ) {

            const id =
                sensor.id;


            document.getElementById(
                `name-${id}`
            ).textContent =
                sensor.nombre;


            document.getElementById(
                `value-${id}`
            ).textContent =
                sensor.conectado
                ?
                sensor.valor
                :
                "--";


            document.getElementById(
                `average-${id}`
            ).textContent =
                sensor.conectado
                ?
                Number(
                    sensor.promedio
                ).toFixed(1)
                :
                "--";


            document.getElementById(
                `countdown-${id}`
            ).textContent =
                formatoTiempo(
                    restante
                );


            document.getElementById(
                `progress-${id}`
            ).style.width =
                Math.min(
                    100,
                    Math.max(
                        0,
                        porcentaje
                    )
                )
                +
                "%";


            const conexion =
                document.getElementById(
                    `connection-${id}`
                );


            const estado =
                document.getElementById(
                    `state-${id}`
                );


            if (!sensor.conectado) {

                conexion.textContent =
                    "Sin conexión";

                estado.className =
                    "state offline";

                estado.textContent =
                    "DESCONECTADO";

            }

            else if (sensor.alerta) {

                conexion.textContent =
                    "Conectado";

                estado.className =
                    "state alert";

                estado.textContent =
                    "ALERTA";

            }

            else {

                conexion.textContent =
                    "Conectado";

                estado.className =
                    "state normal";

                estado.textContent =
                    "NORMAL";
            }
        }

    }

    catch (error) {

        statusDot.className =
            "dot off";

        statusText.textContent =
            "Desconectado";


        for (
            let id = 1;
            id <= 2;
            id++
        ) {

            document.getElementById(
                `connection-${id}`
            ).textContent =
                "Sin conexión";


            document.getElementById(
                `value-${id}`
            ).textContent =
                "--";


            document.getElementById(
                `average-${id}`
            ).textContent =
                "--";


            const estado =
                document.getElementById(
                    `state-${id}`
                );


            estado.className =
                "state offline";

            estado.textContent =
                "DESCONECTADO";
        }


        console.error(
            "Error:",
            error
        );
    }
}


/* ==========================================================
   CARGAR HISTORIAL
   ========================================================== */

async function cargarHistorial() {

    try {

        const respuesta =
            await fetch(
                "/api/promedios?t="
                +
                Date.now(),
                {
                    cache: "no-store"
                }
            );


        if (!respuesta.ok) {

            throw new Error(
                "No se pudo cargar el historial"
            );
        }


        const datos =
            await respuesta.json();


        const body =
            document.getElementById(
                "historyBody"
            );


        body.innerHTML = "";


        if (
            !datos.historial
            ||
            datos.historial.length === 0
        ) {

            body.innerHTML = `

            <tr>

                <td colspan="4">
                    Todavía no hay promedios registrados.
                </td>

            </tr>

            `;

            return;
        }


        for (
            const registro
            of datos.historial
        ) {

            body.innerHTML += `

            <tr>

                <td>
                    ${registro.nombre}
                </td>


                <td>
                    ${Number(
                        registro.promedio
                    ).toFixed(1)}
                </td>


                <td>
                    ${
                        registro.alerta
                        ?
                        "ALERTA"
                        :
                        "NORMAL"
                    }
                </td>


                <td>
                    ${registro.periodo}
                </td>

            </tr>

            `;
        }

    }

    catch (error) {

        console.error(
            "Error cargando historial:",
            error
        );
    }
}


/* ==========================================================
   NAVEGACIÓN
   ========================================================== */

function mostrarLive() {

    document.getElementById(
        "liveView"
    ).classList.remove(
        "hidden"
    );


    document.getElementById(
        "historyView"
    ).classList.remove(
        "active"
    );


    document.getElementById(
        "btnLive"
    ).classList.add(
        "active"
    );


    document.getElementById(
        "btnHistory"
    ).classList.remove(
        "active"
    );
}


function mostrarHistorial() {

    document.getElementById(
        "liveView"
    ).classList.add(
        "hidden"
    );


    document.getElementById(
        "historyView"
    ).classList.add(
        "active"
    );


    document.getElementById(
        "btnLive"
    ).classList.remove(
        "active"
    );


    document.getElementById(
        "btnHistory"
    ).classList.add(
        "active"
    );


    cargarHistorial();
}


/* ==========================================================
   INICIO
   ========================================================== */

crearTarjetas();

actualizar();


// Actualizar la pantalla cada 2 segundos
setInterval(
    actualizar,
    2000
);

</script>


</body>

</html>
"""


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def inicio():

    return render_template_string(
        HTML
    )


# ============================================================
# EJECUCIÓN LOCAL
# ============================================================

if __name__ == "__main__":

    print()
    print("======================================")
    print(" MONITOREO PORTABLE DE CO2")
    print("======================================")
    print(" http://localhost:5000")
    print("======================================")
    print()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
