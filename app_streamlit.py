# -*- coding: utf-8 -*-
"""
Dashboard Streamlit - Sistema de Logística de Monedas Inteligentes
------------------------------------------------------------------
Se ve bien en computador y en celular (diseño responsivo).

Fuentes de datos (barra lateral):
  - Auto        : usa estado.json si lo está escribiendo sim_monedas.py; si no, modo demo.
  - Archivo     : lee estado.json (PyBullet).
  - ESP32 (URL) : lee un JSON con el mismo formato desde la ESP32, p. ej. http://192.168.1.50/datos
  - Demo        : datos de ejemplo para probar el dashboard sin simulación.

Ejecutar:
    pip install streamlit pandas altair
    streamlit run app_streamlit.py --server.address 0.0.0.0

Desde el celular (misma red Wi-Fi): abre http://IP-DEL-COMPUTADOR:8501
"""
import base64
import json
import math
import re
import time
import urllib.request
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

DENOMS = [50, 100, 200, 500, 1000]
COLORES = {50: "#9aa3b2", 100: "#5aa9f0", 200: "#4cc38a", 500: "#f2b73d", 1000: "#ef6f5e"}
PESOS_DEFECTO = {50: 2.0, 100: 3.5, 200: 4.61, 500: 7.14, 1000: 9.95}  # g; $100 provisional
ARCHIVO_DEFECTO = "estado.json"
FRESCURA_S = 10          # un estado.json más viejo que esto se considera "sin actualizar"

# Copia del escenario que genera sim_monedas.py (se usa solo en modo demo)
ESCENARIO_DEF = {
    "camino": [
        [2.270, 0.000], [2.466, 0.000], [2.661, 0.000], [2.857, 0.000],
        [3.052, 0.000], [3.248, 0.000], [3.444, 0.000], [3.639, 0.002],
        [3.822, 0.067], [3.953, 0.211], [4.000, 0.400], [4.000, 0.594],
        [4.000, 0.787], [4.000, 0.981], [4.000, 1.174], [4.000, 1.368],
        [4.000, 1.561], [3.970, 1.753], [3.854, 1.909], [3.678, 1.992],
        [3.488, 2.000], [3.300, 2.000], [3.112, 2.000], [2.922, 2.008],
        [2.746, 2.091], [2.630, 2.247], [2.600, 2.438], [2.600, 2.625],
        [2.600, 2.812], [2.600, 3.000], [2.647, 3.189], [2.778, 3.333],
        [2.961, 3.398], [3.155, 3.400], [3.348, 3.400], [3.542, 3.400],
        [3.735, 3.400], [3.929, 3.400], [4.123, 3.400], [4.200, 3.400]],
    "ancho": 0.7,
    "obstaculos": [
        {"tipo": "muro", "nombre": "Muro", "c": [2.935, 0.0], "yaw": 0.0, "h": [0.03, 0.13], "lat": 0.22, "s": 0.665},
        {"tipo": "gravilla", "nombre": "Gravilla", "c": [3.365, 0.0], "yaw": 0.0, "h": [0.2, 0.32], "lat": 0.0, "s": 1.095},
        {"tipo": "escalera", "nombre": "Escalera", "c": [4.0, 0.981], "yaw": 1.571, "h": [0.485, 0.31], "lat": 0.0, "s": 2.539},
        {"tipo": "muro", "nombre": "Muro", "c": [3.3, 2.0], "yaw": 3.142, "h": [0.03, 0.13], "lat": -0.22, "s": 4.086},
        {"tipo": "muro", "nombre": "Muro", "c": [2.6, 2.7], "yaw": 1.571, "h": [0.03, 0.13], "lat": 0.22, "s": 5.314}],
    "ruta": [
        [2.490, -0.007], [2.710, -0.106], [2.930, -0.130], [3.150, -0.110],
        [3.370, -0.010], [3.590, 0.000], [3.800, 0.054], [3.952, 0.210],
        [4.000, 0.422], [4.000, 0.642], [4.000, 0.862], [4.000, 1.082],
        [4.000, 1.302], [4.000, 1.522], [3.975, 1.739], [3.847, 1.914],
        [3.641, 1.951], [3.426, 1.870], [3.206, 1.870], [2.983, 1.938],
        [2.779, 2.067], [2.639, 2.232], [2.693, 2.446], [2.730, 2.666],
        [2.720, 2.886], [2.634, 3.099], [2.726, 3.291], [2.918, 3.391],
        [3.138, 3.400]],
    "inicio_tanque": [2.6, 0.0],
    "meta": [3.73, 3.4],
    "meta_h": [0.22, 0.38],
    "banda": {"y": 0.0, "x_ini": -0.6, "x_fin": 2.1, "x_tapa": 1.35},
}

ESTADOS_BRAZO = {  # estado del brazo de tapas -> texto
    "REPOSO": "En reposo",
    "BAJAR_A_TAPA": "Tomando una tapa", "AGARRAR_TAPA": "Tomando una tapa",
    "SUBIR_CON_TAPA": "Llevando la tapa", "GIRAR_A_VASO": "Llevando la tapa",
    "BAJAR_A_VASO": "Colocando la tapa", "SOLTAR_TAPA": "Colocando la tapa",
    "SUBIR_SIN_TAPA": "Volviendo", "VOLVER": "Volviendo",
    "TAPANDO": "Colocando la tapa",
}

ESTADOS = {  # estado del tanque -> (texto, color)
    "ESPERANDO_VASO": ("Esperando vaso", "#8a94a6"),
    "ACERCARSE": ("Acercándose al vaso", "#3b82f6"),
    "ALINEAR": ("Alineándose", "#3b82f6"),
    "BAJAR_BRAZO": ("Bajando el brazo", "#f59e0b"),
    "AGARRAR": ("Cerrando la pinza", "#f59e0b"),
    "SUBIR_BRAZO": ("Subiendo el vaso", "#f59e0b"),
    "EN_RUTA": ("En ruta a la meta", "#10b981"),
    "BAJAR_ENTREGA": ("Bajando el vaso", "#f59e0b"),
    "SOLTAR": ("Abriendo la pinza", "#f59e0b"),
    "SUBIR_VACIO": ("Subiendo el brazo", "#f59e0b"),
    "REGRESAR": ("Regresando por otro vaso", "#6366f1"),
    "FIN": ("Meta alcanzada", "#16a34a"),
}


# ============================================================================
# LÓGICA (sin Streamlit, fácil de probar)
# ============================================================================
def pesos_cop(v):
    return "$" + f"{int(round(v)):,}".replace(",", ".")


def _largos(camino):
    return [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(camino[:-1], camino[1:])]


def _pos_en_camino(camino, d):
    """Posición (x, y, yaw) tras recorrer d metros sobre el camino."""
    for a, b, L in zip(camino[:-1], camino[1:], _largos(camino)):
        if d <= L or (a, b) == (camino[-2], camino[-1]):
            f = min(1.0, d / L) if L else 1.0
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                    math.atan2(b[1] - a[1], b[0] - a[0]))
        d -= L
    return camino[-1][0], camino[-1][1], 0.0


def demo_estado(ahora=None):
    """Estado sintético con el mismo formato que escribe sim_monedas.py."""
    esc = ESCENARIO_DEF
    ciclo, v = 130.0, 0.2
    t = (time.time() if ahora is None else ahora) % ciclo
    camino = esc["camino"]
    if esc.get("ruta"):      # ruta real del tanque (con los desvíos junto a los muros)
        camino = [camino[0]] + esc["ruta"] + [[esc["meta"][0] - 0.35, esc["meta"][1]]]
    L = sum(_largos(camino))
    t_listo, t_pick, t_ruta, t_tapas = 62.0, 70.0, 73.0, 52.0
    t_fin = t_ruta + L / v

    secuencia = [200, 50, 500, 200, 1000, 100, 200, 500, 50, 200,
                 500, 1000, 200, 100, 500, 200, 50, 1000, 500, 200]  # cantidades distintas

    def denom_i(i):
        return secuencia[i % len(secuencia)]

    def n_monedas(tt):
        return min(20, int(tt / 2.5))

    def tanque_en(tt):
        ini = esc["inicio_tanque"]
        if tt < t_listo:
            return ini[0], ini[1], math.pi, "ESPERANDO_VASO"
        if tt < t_pick:
            f = (tt - t_listo) / (t_pick - t_listo)
            return (ini[0] + (camino[0][0] - ini[0]) * f, ini[1], math.pi,
                    "ACERCARSE")
        if tt < t_ruta:
            return camino[0][0], camino[0][1], math.pi, "AGARRAR"
        if tt < t_fin:
            x, y, yaw = _pos_en_camino(camino, (tt - t_ruta) * v)
            return x, y, yaw, "EN_RUTA"
        x, y, yaw = _pos_en_camino(camino, L)
        return x, y, yaw, "FIN"

    n = n_monedas(t)
    cuentas = {d: 0 for d in DENOMS}
    for i in range(n):
        cuentas[denom_i(i)] += 1
    x, y, yaw, est = tanque_en(t)
    # un vaso por denominación, que se llena con las monedas que haya de ese valor
    denoms_con = [d for d in DENOMS if cuentas[d] > 0]
    tapadas = min(len(denoms_con), int((t - t_tapas) / 4) + 1) if t >= t_tapas else 0
    vasos = [{"denom": d, "n": cuentas[d], "valor": d * cuentas[d],
              "tapado": i < tapadas, "entregado": est == "FIN"}
             for i, d in enumerate(denoms_con)]
    brazo = {"estado": "TAPANDO" if 0 < t - t_tapas < 4 * len(denoms_con) else "REPOSO",
             "tapas_puestas": tapadas}
    activo = est in ("EN_RUTA", "SOLTAR", "FIN")
    traj = []
    tau = t_ruta
    while activo and tau <= t:
        traj.append([round(tanque_en(tau)[0], 3), round(tanque_en(tau)[1], 3)])
        tau += 0.5
    serie, tau = [], 0.0
    while tau <= t:
        serie.append([round(tau, 1),
                      sum(denom_i(i) for i in range(n_monedas(tau))), n_monedas(tau)])
        tau += 1.5
    return {
        "t": round(t, 1), "origen": "demo",
        "cuentas": {str(d): cuentas[d] for d in DENOMS},
        "valor_total": sum(d * c for d, c in cuentas.items()),
        "monedas_contadas": n, "monedas_lanzadas": n, "monedas_objetivo": 20,
        "precision": 1.0 if n else None,
        "vasos_embalados": tapadas,
        "vasos_entregados": sum(1 for v in vasos if v["entregado"]),
        "total_vasos": len(vasos) if t >= t_tapas else 0, "vasos": vasos,
        "brazo_tapas": brazo,
        "valores_vasos": [v["valor"] for v in vasos],
        "tanque": {"x": round(x, 3), "y": round(y, 3), "yaw": round(yaw, 3),
                   "estado": est,
                   "obstaculos_superados": (
                       len(esc["obstaculos"]) if est == "FIN" else
                       sum(1 for o in esc["obstaculos"]
                           if est == "EN_RUTA" and (t - t_ruta) * v > o["s"] + o["h"][0] + 0.1)),
                   "obstaculos_total": len(esc["obstaculos"]),
                   "colisiones": 0},
        "meta_alcanzada": est == "FIN",
        "trayectoria": traj, "serie": serie, "escenario": esc,
    }


def leer_estado(origen, ruta, url):
    """Devuelve (estado, etiqueta_fuente, aviso)."""
    def archivo():
        pth = Path(ruta)
        edad = time.time() - pth.stat().st_mtime
        return json.loads(pth.read_text(encoding="utf-8")), edad

    try:
        if origen == "Demo":
            return demo_estado(), "demo", None
        if origen == "ESP32 (URL)":
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.loads(r.read().decode("utf-8")), "esp32", None
        est, edad = archivo()
        if origen == "Auto" and edad > FRESCURA_S:
            return demo_estado(), "demo", "No hay simulación activa: mostrando datos de demostración."
        if edad > FRESCURA_S:
            return est, "detenido", f"El archivo no se actualiza hace {int(edad)} s."
        return est, "vivo", None
    except Exception as e:  # archivo ausente, URL caída, JSON a medias...
        if origen == "Auto":
            return demo_estado(), "demo", "No hay simulación activa: mostrando datos de demostración."
        return None, "error", f"No se pudo leer la fuente: {e}"


def derivar(e, pesos):
    cuentas = {int(k): int(v) for k, v in e.get("cuentas", {}).items()}
    for d in DENOMS:
        cuentas.setdefault(d, 0)
    cant = sum(cuentas.values())
    return {
        "cuentas": cuentas,
        "cantidad": cant,
        "valor": sum(d * n for d, n in cuentas.items()),
        "peso": sum(pesos[d] * n for d, n in cuentas.items()),
    }


def responder(q, e, pesos):
    """Asistente por reglas. Sustituible por un LLM si se desea."""
    t = q.lower()
    t2 = t.replace(".", "").replace(",", "")
    der = derivar(e, pesos)
    c = der["cuentas"]
    tq = e.get("tanque", {})
    est = ESTADOS.get(tq.get("estado", ""), ("desconocido", ""))[0].lower()

    pedidas = [int(x) for x in re.findall(r"\b(50|100|200|500|1000)\b", t2)]
    if re.search(r"\bmil\b", t2):
        pedidas.append(1000)
    if pedidas and not re.search(r"peso|pesa", t):
        d = pedidas[0]
        return (f"Se han contado {c[d]} monedas de {pesos_cop(d)}, que suman "
                f"{pesos_cop(c[d] * d)}.")
    if re.search(r"peso|pesa|gramo", t):
        return (f"El peso estimado de las monedas procesadas es de {der['peso']:.1f} gramos "
                f"(calculado con el peso nominal de cada denominación).")
    if re.search(r"tapa|brazo", t):
        bz = e.get("brazo_tapas") or {}
        return (f"El brazo de tapas ha colocado {bz.get('tapas_puestas', 0)} de "
                f"{e.get('total_vasos', 0)} tapas. Estado: "
                f"{ESTADOS_BRAZO.get(bz.get('estado', ''), 'desconocido').lower()}.")
    if "vaso" in t or "embal" in t:
        vs = e.get("vasos") or []
        if not vs:
            return "Todavía no hay vasos embalados: se embalan al terminar el conteo."
        det = "; ".join(f"{pesos_cop(v['denom'])}: {v['n']} monedas ({pesos_cop(v['valor'])})"
                        for v in vs)
        return (f"Hay {len(vs)} vasos, uno por denominación, y se han entregado "
                f"{e.get('vasos_entregados', 0)}. Contenido: {det}.")
    if re.search(r"tanque|ruta|obst|meta|robot|pinza|estado|colisi", t):
        meta = "Ya llegó a la meta." if e.get("meta_alcanzada") else "Todavía no llega a la meta."
        return (f"Estado del tanque: {est}. Ha superado {tq.get('obstaculos_superados', 0)} de {tq.get('obstaculos_total', 5)} "
                f"obstáculos, con {tq.get('colisiones', 0)} colisiones. {meta}")
    if re.search(r"precisi|acierto|error|exacti", t):
        pr = e.get("precision")
        return ("Aún no hay monedas contadas." if pr is None
                else f"La precisión de clasificación es del {pr * 100:.1f} %.")
    if re.search(r"valor|total|dinero|plata|cu[aá]nto|suma", t):
        return (f"El valor total contado es {pesos_cop(der['valor'])}, con "
                f"{der['cantidad']} monedas.")
    if re.search(r"hola|ayuda|qu[eé] puedes|buenas", t):
        return ("Puedo contarte el valor total, la cantidad por moneda, el peso estimado, los "
                "vasos embalados y el estado del tanque. Pregúntame lo que necesites.")
    return resumen(e, pesos)


def resumen(e, pesos):
    der = derivar(e, pesos)
    tq = e.get("tanque", {})
    est = ESTADOS.get(tq.get("estado", ""), ("desconocido", ""))[0].lower()
    return (f"Hasta ahora se han contado {der['cantidad']} monedas por un valor de "
            f"{pesos_cop(der['valor'])}, con un peso estimado de {der['peso']:.1f} gramos. "
            f"Hay {e.get('vasos_embalados', 0)} vasos tapados (uno por denominación) y "
            f"{e.get('vasos_entregados', 0)} entregados. Estado del tanque: {est}.")


# ============================================================================
# VISUALES
# ============================================================================
CSS = """
<style>
.block-container{padding-top:1.1rem;padding-bottom:2.5rem;max-width:1180px}
h1,h2,h3{letter-spacing:-.01em}
.hero{border-radius:18px;padding:18px 22px;margin-bottom:14px;color:#fff;
  background:linear-gradient(120deg,#0f3d78 0%,#1b6ec2 55%,#2aa6a0 100%);
  box-shadow:0 8px 24px rgba(15,61,120,.25)}
.hero h1{font-size:1.55rem;margin:0;color:#fff}
.hero p{margin:.25rem 0 0;opacity:.9;font-size:.92rem}
.chip{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.78rem;
  font-weight:600;margin-right:6px;background:rgba(255,255,255,.18);color:#fff}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:4px 0 16px}
.kpi{border:1px solid rgba(128,128,128,.25);background:rgba(128,128,128,.07);
  border-radius:16px;padding:13px 15px}
.kpi .l{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;opacity:.65}
.kpi .v{font-size:1.75rem;font-weight:750;line-height:1.15}
.kpi .s{font-size:.78rem;opacity:.6;margin-top:2px}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;font-size:.85rem;
  font-weight:650;color:#fff}
.vasos span{display:inline-block;margin:3px 6px 3px 0;padding:5px 12px;border-radius:12px;
  background:rgba(242,183,61,.18);border:1px solid rgba(242,183,61,.5);font-weight:600}
.aviso{border-left:4px solid #f59e0b;background:rgba(245,158,11,.12);padding:8px 12px;
  border-radius:8px;margin-bottom:10px;font-size:.9rem}
img.ruta{width:100%;max-width:470px;display:block;margin:0 auto;border-radius:16px;
  border:1px solid rgba(128,128,128,.25)}
</style>
"""


def kpi(label, valor, sub=""):
    return (f'<div class="kpi"><div class="l">{label}</div><div class="v">{valor}</div>'
            f'<div class="s">{sub}</div></div>')


def mostrar_altair(chart):
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:
        st.altair_chart(chart, use_container_width=True)


def svg_ruta(e):
    esc = e.get("escenario") or ESCENARIO_DEF
    camino = esc["camino"]
    ancho = esc.get("ancho", 0.70)
    xs = [p[0] for p in camino]
    ys = [p[1] for p in camino]
    mx, my = esc["meta"]
    bd = esc.get("banda") or ESCENARIO_DEF["banda"]
    X0, X1 = bd["x_fin"] - 0.30, max(xs + [mx + 0.9]) + 0.35
    Y0, Y1 = min(ys) - 0.55, max(ys + [my]) + 0.55
    PX = 150
    W, H = (X1 - X0) * PX, (Y1 - Y0) * PX

    def sx(x):
        return (x - X0) * PX

    def sy(y):
        return (Y1 - y) * PX

    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in camino)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}">',
         f'<rect width="{W:.0f}" height="{H:.0f}" rx="18" fill="#e9efe9"/>']
    # banda (parte visible: el final, de donde recoge el tanque)
    s.append(f'<rect x="0" y="{sy(bd["y"]) - 0.08 * PX:.1f}" width="{sx(bd["x_fin"]):.1f}" '
             f'height="{0.16 * PX:.1f}" fill="#1f2125"/>')
    s.append(f'<text x="8" y="{sy(bd["y"]) + 0.08 * PX + 16:.1f}" font-size="13" '
             f'fill="#445" font-family="sans-serif">Banda</text>')
    # carretera: bordillo blanco con tramos rojos, asfalto y línea central discontinua
    ancho_b = (ancho + 0.14) * PX
    s.append(f'<polyline points="{pts}" fill="none" stroke="#f4f4f4" '
             f'stroke-width="{ancho_b:.0f}" stroke-linejoin="round"/>')
    s.append(f'<polyline points="{pts}" fill="none" stroke="#e8574b" '
             f'stroke-width="{ancho_b:.0f}" stroke-linejoin="round" stroke-dasharray="22 22"/>')
    s.append(f'<polyline points="{pts}" fill="none" stroke="#3a3f47" '
             f'stroke-width="{ancho * PX:.0f}" stroke-linejoin="round"/>')
    s.append(f'<polyline points="{pts}" fill="none" stroke="#f4f4f4" stroke-width="2.5" '
             f'stroke-dasharray="12 12"/>')
    # meta
    mh = esc.get("meta_h", [0.22, 0.38])
    s.append(f'<rect x="{sx(mx - mh[0]):.1f}" y="{sy(my + mh[1]):.1f}" '
             f'width="{2 * mh[0] * PX:.1f}" height="{2 * mh[1] * PX:.1f}" rx="6" fill="#2fb36a" '
             f'opacity=".9"/>')
    s.append(f'<text x="{sx(mx + mh[0] + 0.12):.1f}" y="{sy(my) + 9:.1f}" font-size="28" '
             f'text-anchor="middle">🏁</text>')
    # obstáculos (rotados según el rumbo de la carretera)
    for i, o in enumerate(esc.get("obstaculos", []), 1):
        (cx, cy), (hl, ha) = o["c"], o["h"]
        yaw = o.get("yaw", 0.0)
        lat = o.get("lat", 0.0)
        px, py = cx - lat * math.sin(yaw), cy + lat * math.cos(yaw)
        deg = -math.degrees(yaw)
        w, h = 2 * hl * PX, 2 * ha * PX
        g = [f'<g transform="translate({sx(px):.1f},{sy(py):.1f}) rotate({deg:.1f})">']
        if o["tipo"] == "muro":
            g.append(f'<rect x="{-w / 2 - 2:.1f}" y="{-h / 2:.1f}" width="{w + 4:.1f}" height="{h:.1f}" '
                     f'rx="3" fill="#b04a38" stroke="#7d2f22" stroke-width="2"/>')
        elif o["tipo"] == "gravilla":
            g.append(f'<rect x="{-w / 2:.1f}" y="{-h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
                     f'rx="4" fill="#8a8178"/>')
            for k in range(26):   # piedras
                a = (k * 37 % 100) / 100
                b = (k * 61 % 100) / 100
                g.append(f'<circle cx="{(a - 0.5) * w:.1f}" cy="{(b - 0.5) * h:.1f}" r="2.6" '
                         f'fill="{"#5d5d62" if k % 2 else "#b3afa6"}"/>')
        else:  # escalera
            g.append(f'<rect x="{-w / 2:.1f}" y="{-h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
                     f'fill="#bdbdb8" stroke="#8d8d89" stroke-width="2"/>')
            for fx in (0, 1 / 6, 2 / 6, 4 / 6, 5 / 6, 1):
                g.append(f'<line x1="{-w / 2 + fx * w:.1f}" y1="{-h / 2:.1f}" x2="{-w / 2 + fx * w:.1f}" '
                         f'y2="{h / 2:.1f}" stroke="#e7b81c" stroke-width="3"/>')
        g.append('</g>')
        s.append("".join(g))
        etiqueta = {"muro": "Muro", "gravilla": "Gravilla", "escalera": "Escalera"}[o["tipo"]]
        for trazo, color in ((3, "#fff"), (0, "#222")):      # contorno blanco + texto
            s.append(f'<text x="{sx(px):.1f}" y="{sy(py) - h / 2 - 5:.1f}" font-size="12" '
                     f'font-weight="700" fill="{color}" text-anchor="middle" '
                     f'font-family="sans-serif" stroke="{color}" stroke-width="{trazo}" '
                     f'stroke-linejoin="round">{i}. {etiqueta}</text>')
    # trayectoria recorrida
    tr = e.get("trayectoria") or []
    if len(tr) > 1:
        tp = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in tr)
        s.append(f'<polyline points="{tp}" fill="none" stroke="#ff8a00" stroke-width="4" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')
    # tanque
    t = e.get("tanque", {})
    if "x" in t:
        deg = -math.degrees(t.get("yaw", 0.0))
        s.append(f'<g transform="translate({sx(t["x"]):.1f},{sy(t["y"]):.1f}) rotate({deg:.1f}) '
                 f'scale({PX / 190:.3f})">'
                 f'<rect x="{-0.14 * 190:.1f}" y="{-0.105 * 190:.1f}" width="{0.28 * 190:.1f}" '
                 f'height="{0.045 * 190:.1f}" rx="6" fill="#16181b"/>'
                 f'<rect x="{-0.14 * 190:.1f}" y="{0.06 * 190:.1f}" width="{0.28 * 190:.1f}" '
                 f'height="{0.045 * 190:.1f}" rx="6" fill="#16181b"/>'
                 f'<rect x="{-0.12 * 190:.1f}" y="{-0.07 * 190:.1f}" width="{0.26 * 190:.1f}" '
                 f'height="{0.14 * 190:.1f}" rx="5" fill="#1d6fdc"/>'
                 f'<rect x="{0.14 * 190:.1f}" y="{-0.065 * 190:.1f}" width="{0.09 * 190:.1f}" '
                 f'height="{0.012 * 190 * 2:.1f}" fill="#f28c1b"/>'
                 f'<rect x="{0.14 * 190:.1f}" y="{0.04 * 190:.1f}" width="{0.09 * 190:.1f}" '
                 f'height="{0.012 * 190 * 2:.1f}" fill="#f28c1b"/></g>')
    s.append("</svg>")
    return "".join(s)


def imagen_svg(svg):
    b64 = base64.b64encode(svg.encode("utf-8")).decode()
    return f'<img class="ruta" src="data:image/svg+xml;base64,{b64}"/>'


def boton_voz(texto, etiqueta="🔊 Escuchar"):
    js = json.dumps(texto)
    html = ("<button id='b' style=\"padding:9px 16px;border:0;border-radius:10px;"
            "background:#1b6ec2;color:#fff;font:600 15px system-ui;cursor:pointer\">"
            f"{etiqueta}</button><script>document.getElementById('b').onclick=function(){{"
            f"var u=new SpeechSynthesisUtterance({js});u.lang='es-CO';u.rate=1;"
            "speechSynthesis.cancel();speechSynthesis.speak(u);};</script>")
    components.html(html, height=54)


def buscar_html_3d():
    base = Path(__file__).parent
    for nombre in ("minitanque.html", "minitanque (1).html", "minitanque__1_.html"):
        f = base / nombre
        if f.exists():
            return f.read_text(encoding="utf-8")
    return None


# ============================================================================
# APLICACIÓN
# ============================================================================
def main():
    st.set_page_config(page_title="Logística de Monedas Inteligentes", page_icon="🪙",
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.header("⚙️ Configuración")
        st.radio("Fuente de datos", ["Auto", "Archivo", "ESP32 (URL)", "Demo"], key="origen")
        st.text_input("Archivo estado.json", ARCHIVO_DEFECTO, key="ruta_json")
        st.text_input("URL de la ESP32", "http://192.168.1.50/datos", key="url_esp")
        st.slider("Actualizar cada (s)", 1, 5, 1, key="refresco")
        with st.expander("Peso aproximado por moneda (g)"):
            for d in DENOMS:
                st.number_input(f"${d}", 0.0, 50.0, PESOS_DEFECTO[d], 0.01, key=f"peso_{d}")
        st.caption("El peso de $100 es provisional: pésalo y corrígelo aquí.")

    def pesos():
        return {d: st.session_state.get(f"peso_{d}", PESOS_DEFECTO[d]) for d in DENOMS}

    def obtener():
        return leer_estado(st.session_state.get("origen", "Auto"),
                           st.session_state.get("ruta_json", ARCHIVO_DEFECTO),
                           st.session_state.get("url_esp", ""))

    def fragmento(fn):
        every = st.session_state.get("refresco", 1)
        return st.fragment(run_every=every)(fn) if hasattr(st, "fragment") else fn

    # ----------------------- ENCABEZADO -----------------------
    est0, fuente0, _ = obtener()
    chips = {"vivo": "● En vivo · PyBullet", "esp32": "● En vivo · ESP32",
             "demo": "● Modo demo", "detenido": "● Sin actualizar", "error": "● Sin conexión"}
    st.markdown(
        f'<div class="hero"><span class="chip">{chips.get(fuente0, fuente0)}</span>'
        f'<h1>🪙 Sistema de Logística de Monedas Inteligentes</h1>'
        f'<p>Contador · Transporte y embalaje · Minitanque con pinza · Pista con obstáculos</p>'
        f'</div>', unsafe_allow_html=True)

    tab_dash, tab_ruta, tab_3d, tab_chat = st.tabs(
        ["📊 Dashboard", "🗺️ Ruta", "🎮 Simulación 3D", "🤖 Asistente"])

    # ----------------------- DASHBOARD -----------------------
    def vista_dashboard():
        e, fuente, aviso = obtener()
        if e is None:
            st.error(aviso)
            return
        if aviso:
            st.markdown(f'<div class="aviso">{aviso}</div>', unsafe_allow_html=True)
        der = derivar(e, pesos())
        tq = e.get("tanque", {})
        etiqueta, color = ESTADOS.get(tq.get("estado", ""), ("—", "#8a94a6"))
        prec = e.get("precision")
        st.markdown(
            '<div class="kpis">'
            + kpi("Valor total", pesos_cop(der["valor"]), "monedas procesadas")
            + kpi("Monedas", der["cantidad"],
                  f'de {e.get("monedas_objetivo", "—")} previstas')
            + kpi("Peso estimado", f'{der["peso"]:.1f} g', "calculado por denominación")
            + kpi("Vasos entregados",
                  f'{e.get("vasos_entregados", 0)} / {e.get("vasos_embalados", 0)}',
                  "un vaso por denominación")
            + kpi("Precisión", "—" if prec is None else f"{prec * 100:.0f} %",
                  "clasificación")
            + kpi("Tapas puestas", f'{(e.get("brazo_tapas") or {}).get("tapas_puestas", 0)}',
                  f'de {e.get("total_vasos", 0)} vasos')
            + kpi("Obstáculos", f'{tq.get("obstaculos_superados", 0)} / {tq.get("obstaculos_total", 5)}',
                  f'{tq.get("colisiones", 0)} colisiones')
            + '</div>', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        etiquetas = [pesos_cop(d) for d in DENOMS]
        df = pd.DataFrame({"Moneda": etiquetas,
                           "Cantidad": [der["cuentas"][d] for d in DENOMS],
                           "Valor": [pesos_cop(der["cuentas"][d] * d) for d in DENOMS]})
        with c1:
            st.subheader("Monedas por denominación")
            base = alt.Chart(df).encode(
                x=alt.X("Moneda:N", sort=None, title=None),
                y=alt.Y("Cantidad:Q", title=None),
                color=alt.Color("Moneda:N", legend=None,
                                scale=alt.Scale(domain=etiquetas,
                                                range=[COLORES[d] for d in DENOMS])),
                tooltip=["Moneda", "Cantidad", "Valor"])
            graf = alt.layer(
                base.mark_bar(cornerRadiusTopLeft=8, cornerRadiusTopRight=8),
                base.mark_text(dy=-8, fontWeight="bold").encode(text="Cantidad:Q"),
            ).properties(height=260)
            mostrar_altair(graf)
        with c2:
            st.subheader("Valor acumulado")
            serie = e.get("serie") or []
            if len(serie) > 1:
                ds = pd.DataFrame(serie, columns=["t", "Valor", "Monedas"])
                linea = alt.Chart(ds).encode(
                    x=alt.X("t:Q", title="tiempo (s)"),
                    y=alt.Y("Valor:Q", title="COP"),
                    tooltip=["t", "Valor", "Monedas"])
                mostrar_altair((linea.mark_area(opacity=0.25, color="#1b6ec2",
                                                interpolate="step-after")
                                + linea.mark_line(color="#1b6ec2", interpolate="step-after")
                                ).properties(height=260))
            else:
                st.info("Esperando datos…")

        c3, c4 = st.columns(2)
        with c3:
            st.subheader("Detalle")
            st.table(df.set_index("Moneda").assign(
                **{"Peso (g)": [f"{der['cuentas'][d] * pesos()[d]:.1f}" for d in DENOMS]}))
        with c4:
            st.subheader("Minitanque")
            st.markdown(f'<span class="badge" style="background:{color}">{etiqueta}</span>',
                        unsafe_allow_html=True)
            tot_ob = tq.get("obstaculos_total", 5)
            st.progress(min(1.0, tq.get("obstaculos_superados", 0) / tot_ob),
                        text="Avance por la pista")
            bz = e.get("brazo_tapas") or {}
            st.subheader("Brazo de tapas")
            st.markdown(
                f'<span class="badge" style="background:#b8741a">'
                f'{ESTADOS_BRAZO.get(bz.get("estado", ""), "—")}</span>',
                unsafe_allow_html=True)
            st.subheader("Vasos embalados")
            vs = e.get("vasos") or [{"denom": None, "n": None, "valor": v, "entregado": False}
                                    for v in e.get("valores_vasos", [])]
            if vs:
                chips = []
                for v in vs:
                    nombre = f"{pesos_cop(v['denom'])} · {v['n']} monedas = " if v["denom"] else ""
                    marca = (" ✅" if v.get("entregado")
                             else " 🔒 con tapa" if v.get("tapado") else "")
                    chips.append(f"<span>🥛 {nombre}{pesos_cop(v['valor'])}{marca}</span>")
                st.markdown('<div class="vasos">' + "".join(chips) + "</div>",
                            unsafe_allow_html=True)
            else:
                st.caption("Los vasos se embalan al terminar el conteo.")

    # ----------------------- RUTA -----------------------
    def vista_ruta():
        e, fuente, aviso = obtener()
        if e is None:
            st.error(aviso)
            return
        tq = e.get("tanque", {})
        etiqueta, color = ESTADOS.get(tq.get("estado", ""), ("—", "#8a94a6"))
        a, b = st.columns([3, 2])
        with a:
            st.markdown(imagen_svg(svg_ruta(e)), unsafe_allow_html=True)
        with b:
            st.subheader("Seguimiento de la ruta")
            st.markdown(f'<span class="badge" style="background:{color}">{etiqueta}</span>',
                        unsafe_allow_html=True)
            st.markdown(
                '<div class="kpis">'
                + kpi("Posición", f'{tq.get("x", 0):.2f}, {tq.get("y", 0):.2f} m')
                + kpi("Obstáculos superados", f'{tq.get("obstaculos_superados", 0)} / {tq.get("obstaculos_total", 5)}')
                + kpi("Colisiones", tq.get("colisiones", 0))
                + kpi("Meta", "✅ Sí" if e.get("meta_alcanzada") else "⏳ No")
                + '</div>', unsafe_allow_html=True)
            sv = tq.get("servos")
            if sv:
                st.markdown(
                    '<div class="kpis">'
                    + kpi("Servo 1 · brazo", f'{sv.get("brazo_deg", 0):.0f}°', "hombro")
                    + kpi("Servo 2 · dedo izq.", f'{sv.get("pinza_izq_deg", 0):.0f}°', "pinza")
                    + kpi("Servo 3 · dedo der.", f'{sv.get("pinza_der_deg", 0):.0f}°', "pinza")
                    + '</div>', unsafe_allow_html=True)
            st.caption("Línea naranja: trayectoria recorrida. Muros, gravilla y escalera: obstáculos.")

    # ----------------------- ASISTENTE -----------------------
    def vista_asistente():
        st.subheader("Asistente del proyecto")
        e, _, _ = obtener()
        if e is not None:
            narracion = resumen(e, pesos())
            st.caption("Resumen hablado del proyecto:")
            boton_voz(narracion, "🔊 Narrar estado del proyecto")
        if "chat" not in st.session_state:
            st.session_state.chat = [("assistant",
                                      "¡Hola! Pregúntame por el valor total, las monedas, "
                                      "el peso, los vasos o el tanque.")]
        for rol, msg in st.session_state.chat:
            with st.chat_message(rol):
                st.write(msg)
        q = st.chat_input("Escribe tu pregunta…")
        if q:
            e2, _, _ = obtener()
            resp = responder(q, e2, pesos()) if e2 else "No tengo datos en este momento."
            st.session_state.chat += [("user", q), ("assistant", resp)]
            with st.chat_message("user"):
                st.write(q)
            with st.chat_message("assistant"):
                st.write(resp)
            boton_voz(resp)

    with tab_dash:
        fragmento(vista_dashboard)()
    with tab_ruta:
        fragmento(vista_ruta)()
    with tab_3d:
        html3d = buscar_html_3d()
        if html3d:
            st.caption("Simulación 3D interactiva (Three.js). En el celular, gira la pantalla "
                       "y usa el modo autónomo. Requiere internet para cargar la librería 3D.")
            components.html(html3d, height=680, scrolling=False)
        else:
            st.info("Coloca minitanque.html junto a app_streamlit.py para verlo aquí.")
    with tab_chat:
        vista_asistente()


if __name__ == "__main__":
    main()
