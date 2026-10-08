#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulación PyBullet - Sistema de Logística de Monedas Inteligentes (v3)
-----------------------------------------------------------------------
Flujo (todo alineado sobre el eje x):

  SEPARADOR  ->  BANDA  ->  BRAZO DE TAPAS  ->  fin de banda  ->  MINITANQUE  ->  META

  1. Separador de monedas (arriba de la banda): 5 canales (50, 100, 200, 500 y
     1000 COP, serie 2012). La clasificación usa el ANCHO REAL de cada hueco
     contra el diámetro de la moneda. Un marco rojo (sensor tipo CNY70 / barrera)
     cuenta cada moneda que cruza su canal. Debajo de cada canal hay UN VASO de
     esa denominación sobre la banda: la moneda cae al vaso y este se va llenando
     (más o menos monedas según cuántas haya de ese valor).
  2. Banda transportadora: al terminar el conteo, los vasos avanzan.
  3. Brazo de tapas: junto a la banda, toma una tapa del almacén y la pone al vaso.
  4. Minitanque con brazo y pinza (3 servos): recoge cada vaso tapado al final de la
     banda, recorre la carretera curva (esquiva 3 muros de ladrillo y CRUZA una zona de
     gravilla y una escalera), lo deja en la meta y regresa por el siguiente. Servo 1 = brazo (sube/baja); servos 2 y 3 = dedos de la pinza
     (cierran/abren, uno por lado).
  5. Cada ~0.25 s se escribe estado.json, que lee el dashboard de Streamlit
     (en el sistema real ese JSON lo entregaría la ESP32 por Wi-Fi).

Simplificaciones (para documentar en el informe):
  - La separación por huecos se calcula con la lógica de los anchos; la moneda
    se suelta directamente sobre el canal que le corresponde.
  - Al llegar al vaso la moneda se retira de la simulación y se "apila" una capa
    visual de monedas (máximo N_CAPAS capas visibles); el conteo real es el del JSON.
  - Las tapas del brazo y del vaso son visuales (aparecen/desaparecen).
  - La sujeción del vaso por el tanque se hace con un constraint fijo al carro del
    brazo (el vaso sube y baja con el brazo). Los dedos aprietan al vaso físicamente.
  - El servo del brazo se modela como un deslizador vertical (mástil + carro), para
    que la pinza siempre quede horizontal y el vaso no se incline.
  - La carretera, las líneas y los adornos son solo visuales (sin colisión).

Uso:
    python sim_monedas.py                 # con ventana gráfica
    python sim_monedas.py --sin-gui       # sin ventana (pruebas)
    python sim_monedas.py --monedas 30
    python sim_monedas.py --velocidad 3   # ventana 3 veces más rápida
"""
import argparse
import bisect
import json
import math
import os
import random
import tempfile
import time
from pathlib import Path

import pybullet as p
import pybullet_data

# ----------------------------------------------------------------------------
# PARÁMETROS
# ----------------------------------------------------------------------------
DT = 1.0 / 240.0
ARCHIVO_ESTADO = Path("estado.json")
PERIODO_JSON = 0.25          # s de simulación entre escrituras del JSON
MAX_HIST = 800               # puntos máximos de trayectoria y serie temporal

# Monedas colombianas serie 2012: diámetro (mm), espesor (mm), peso (g)
# OJO: el peso de $100 es un VALOR PROVISIONAL, mídelo con una balanza.
MONEDAS = {
    50:   {"d": 17.0, "e": 1.30, "peso": 2.00},
    100:  {"d": 20.3, "e": 1.50, "peso": 3.50},
    200:  {"d": 22.4, "e": 1.70, "peso": 4.61},
    500:  {"d": 23.7, "e": 2.05, "peso": 7.14},
    1000: {"d": 26.7, "e": 2.70, "peso": 9.95},
}
DENOMS = [50, 100, 200, 500, 1000]
ANCHOS_HUECOS = [17.4, 20.7, 22.8, 24.1]   # mm; el canal de $1000 es el final
TOLERANCIA_MM = 0.10                       # variación de diámetro de fabricación
ESC_MONEDA = 3.0

# Paleta
COLOR_DENOM = {
    50:   [0.72, 0.74, 0.78, 1],
    100:  [0.50, 0.74, 0.96, 1],
    200:  [0.52, 0.85, 0.58, 1],
    500:  [0.98, 0.80, 0.32, 1],
    1000: [0.96, 0.52, 0.42, 1],
}
C_PISO = [0.88, 0.91, 0.88, 1]
C_MESA = [0.94, 0.95, 0.97, 1]
C_ACERO = [0.62, 0.66, 0.72, 1]
C_OSCURO = [0.11, 0.12, 0.14, 1]
C_ASFALTO = [0.23, 0.25, 0.29, 1]
C_LINEA = [0.97, 0.97, 0.97, 1]
C_LADRILLO = [0.68, 0.29, 0.22, 1]
C_MORTERO = [0.82, 0.76, 0.68, 1]
C_VIDRIO = [0.75, 0.88, 0.98, 0.35]
C_TANQUE = [0.15, 0.45, 0.85, 1]
C_ACENTO = [0.95, 0.55, 0.10, 1]
C_META = [0.20, 0.68, 0.38, 1]

# Banda (eje x, y = 0). El separador está encima, alineado con la banda.
DX = 0.8                     # metros que se alargó la banda (y todo lo que va después)
BANDA_X_INI, BANDA_X_FIN_VISUAL = -0.6, 1.3 + DX
BANDA_CENTRO = ((BANDA_X_INI + BANDA_X_FIN_VISUAL) / 2, 0.0, 0.03)
BANDA_MEDIO = ((BANDA_X_FIN_VISUAL - BANDA_X_INI) / 2, 0.08, 0.03)
Z_TOPE_BANDA = 0.06
V_BANDA = 0.12               # m/s
X_TAPA = 0.85 + 0.5          # estación del brazo de tapas (más lejos del separador)
X_FIN_BANDA = 1.24 + DX      # donde se detiene el primer vaso (recoge el tanque)
SEP_VASOS = 0.08
R_VASO, H_VASO = 0.03, 0.08
N_CAPAS = 8                  # capas visuales de monedas en el vaso

# Separador (encima de la banda)
X_CANALES = [-0.30, -0.15, 0.0, 0.15, 0.30]
Z_SPAWN = 0.64
Z_SENSOR = 0.25
Z_ENTRADA_VASO = Z_TOPE_BANDA + H_VASO + 0.03   # la moneda "entra" al vaso

# Brazo de tapas (junto a la banda)
BRAZO_BASE = (X_TAPA, 0.30)
L_BRAZO = 0.30
YAW_ALMACEN = math.pi / 2    # apunta hacia +y: almacén de tapas
YAW_VASO = -math.pi / 2      # apunta hacia -y: vaso sobre la banda
ALMACEN = (X_TAPA, BRAZO_BASE[1] + L_BRAZO)
SLIDE_BAJO = 0.018
J_YAW, J_SLIDE, J_DEDO_I, J_DEDO_D, LINK_TAPA_BRAZO = 1, 2, 3, 4, 5

# Tanque
R_RUEDA = 0.04
Z_RUEDA = 0.03               # eje de las ruedas bajo el centro del chasis (holgura al suelo = 0.04 m)
SEP_ORUGAS = 0.19            # distancia entre orugas (centro a centro)
FUERZA_RUEDA = 6.0
V_MAX, W_MAX = 0.30, 1.6
TANQUE_INICIO = (2.60, 0.0)
PICK_POS = (X_FIN_BANDA + 0.23, 0.0)   # el vaso queda a 0.23 m delante del tanque
PICK_YAW = math.pi

# Servos del tanque: 1 de brazo (sube/baja) + 2 de pinza (un dedo cada uno)
J_BRAZO_T, J_MUNECA_T, J_DEDO_T_I, J_DEDO_T_D = 6, 7, 8, 9
BRAZO_T_ARRIBA = 0.85        # rad: brazo levantado (0 = brazo horizontal, pinza al nivel del vaso)
DEDO_ABIERTO = 0.35          # rad (dedos hacia afuera)
DEDO_CERRADO = -0.45         # rad (dedos hacia adentro, aprietan el vaso)
DEDO_LIM = 0.60
L_BRAZO, L_DEDO, Y_DEDO = 0.14, 0.09, 0.06   # largo del brazo, largo de dedo, separación de bisagras
OFF_PALMA = 0.05             # la pinza va adelantada de la muñeca: el brazo no roza el vaso

# ----------------------------------------------------------------------------
# PISTA: carretera curva con bordillos y 5 obstáculos
#   1 muro de ladrillo, 2 gravilla, 3 escalera (3 subidas + plataforma + 3 bajadas),
#   4 y 5 muros de ladrillo. El tanque esquiva los muros y CRUZA gravilla y escalera.
# ----------------------------------------------------------------------------
ANCHO_CARRETERA = 0.70
RADIO_CURVA = 0.40
ESQUINAS = [PICK_POS, (4.00, 0.00), (4.00, 2.00), (2.60, 2.00), (2.60, 3.40), (4.20, 3.40)]
DODGE = 0.13                 # desvío lateral de la ruta junto a un muro (m)
MURO_LARGO, MURO_ANCHO, MURO_ALTO = 0.06, 0.26, 0.20   # espesor, ancho que tapa, alto
ESC_ALTO, ESC_HUELLA, ESC_N, ESC_PLAT = 0.02, 0.12, 3, 0.25   # contrahuella, huella, nº, plataforma
ESC_ANCHO = 0.62
GRAV_LARGO, GRAV_N = 0.40, 230
X_TANQUE_ENTREGA = 3.38      # fila 1 de entrega: el tanque se detiene aquí (el vaso cae 0.23 m más adelante)
CASILLAS_FILA, SEP_CASILLA, SEP_FILA = 4, 0.14, 0.12   # vasos por fila, separación lateral y entre filas
META_H = (0.22, 0.38)        # semidimensiones de la zona de meta (largo, ancho)

OBSTACULOS_DEF = [
    {"tipo": "muro",     "nombre": "Muro",     "p": (2.95, 0.00), "lado": +1},
    {"tipo": "gravilla", "nombre": "Gravilla", "p": (3.35, 0.00)},
    {"tipo": "escalera", "nombre": "Escalera", "p": (4.00, 1.00)},
    {"tipo": "muro",     "nombre": "Muro",     "p": (3.30, 2.00), "lado": -1},
    {"tipo": "muro",     "nombre": "Muro",     "p": (2.60, 2.70), "lado": +1},
]


def _linea_central(esquinas, radio, ds=0.04):
    """Polilínea suave: tramos rectos unidos por arcos de radio `radio`."""
    pts = [tuple(esquinas[0])]

    def recta(a, b):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / ds)))
        for k in range(1, n + 1):
            f = k / n
            pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))

    cursor = esquinas[0]
    for i in range(1, len(esquinas) - 1):
        p0, p1, p2 = esquinas[i - 1], esquinas[i], esquinas[i + 1]
        l1, l2 = math.hypot(p1[0] - p0[0], p1[1] - p0[1]), math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        u1 = ((p1[0] - p0[0]) / l1, (p1[1] - p0[1]) / l1)
        u2 = ((p2[0] - p1[0]) / l2, (p2[1] - p1[1]) / l2)
        ang = math.atan2(u1[0] * u2[1] - u1[1] * u2[0], u1[0] * u2[0] + u1[1] * u2[1])
        t = radio * math.tan(abs(ang) / 2)
        A = (p1[0] - u1[0] * t, p1[1] - u1[1] * t)
        recta(cursor, A)
        sg = 1 if ang > 0 else -1
        cx, cy = A[0] - sg * u1[1] * radio, A[1] + sg * u1[0] * radio
        th0 = math.atan2(A[1] - cy, A[0] - cx)
        n = max(2, int(math.ceil(radio * abs(ang) / ds)))
        for k in range(1, n + 1):
            th = th0 + ang * k / n
            pts.append((cx + radio * math.cos(th), cy + radio * math.sin(th)))
        cursor = pts[-1]
    recta(cursor, esquinas[-1])
    return pts


CL = _linea_central(ESQUINAS, RADIO_CURVA)
CL_S = [0.0]
for _a, _b in zip(CL[:-1], CL[1:]):
    CL_S.append(CL_S[-1] + math.hypot(_b[0] - _a[0], _b[1] - _a[1]))
LARGO_PISTA = CL_S[-1]
CL_PSI = [math.atan2(CL[min(i + 1, len(CL) - 1)][1] - CL[max(i - 1, 0)][1],
                     CL[min(i + 1, len(CL) - 1)][0] - CL[max(i - 1, 0)][0])
          for i in range(len(CL))]


def pista_en(s):
    """(x, y, rumbo) del centro de la carretera a `s` metros del inicio."""
    s = max(0.0, min(LARGO_PISTA, s))
    i = max(1, min(len(CL) - 1, bisect.bisect_left(CL_S, s)))
    f = (s - CL_S[i - 1]) / max(1e-9, CL_S[i] - CL_S[i - 1])
    x = CL[i - 1][0] + (CL[i][0] - CL[i - 1][0]) * f
    y = CL[i - 1][1] + (CL[i][1] - CL[i - 1][1]) * f
    psi = math.atan2(math.sin(CL_PSI[i - 1]) * (1 - f) + math.sin(CL_PSI[i]) * f,
                     math.cos(CL_PSI[i - 1]) * (1 - f) + math.cos(CL_PSI[i]) * f)
    return x, y, psi


def pista_s(x, y):
    """Distancia a lo largo de la carretera del punto de la línea central más cercano."""
    k = min(range(len(CL)), key=lambda i: (CL[i][0] - x) ** 2 + (CL[i][1] - y) ** 2)
    return CL_S[k]


def a_mundo(x0, y0, psi, lon, lat):
    """Punto a `lon` m a lo largo y `lat` m a la izquierda de (x0, y0) con rumbo psi."""
    return (x0 + lon * math.cos(psi) - lat * math.sin(psi),
            y0 + lon * math.sin(psi) + lat * math.cos(psi))


def _armar_obstaculos():
    obs = []
    for d in OBSTACULOS_DEF:
        s = pista_s(*d["p"])
        x, y, psi = pista_en(s)
        o = dict(d, s=s, c=(x, y), yaw=psi)
        if d["tipo"] == "muro":
            o["largo"], o["h"] = MURO_LARGO, (MURO_LARGO / 2, MURO_ANCHO / 2)
            o["lat"] = d["lado"] * (ANCHO_CARRETERA / 2 - MURO_ANCHO / 2)   # centro lateral
        elif d["tipo"] == "gravilla":
            o["largo"], o["h"] = GRAV_LARGO, (GRAV_LARGO / 2, ANCHO_CARRETERA / 2 - 0.03)
        else:
            o["largo"] = 2 * ESC_N * ESC_HUELLA + ESC_PLAT
            o["h"] = (o["largo"] / 2, ESC_ANCHO / 2)
        obs.append(o)
    return obs


OBST = _armar_obstaculos()
MUROS_DEF = [o for o in OBST if o["tipo"] == "muro"]


def _ruta_pista(ds=0.22):
    """Puntos de la ruta: línea central, con desvío suave al lado libre junto a cada muro."""
    def desvio(s):
        total = 0.0
        for o in MUROS_DEF:
            d = abs(s - o["s"])
            plano, rampa = 0.12, 0.38
            w = 1.0 if d <= plano else max(0.0, (plano + rampa - d) / rampa)
            w = w * w * (3 - 2 * w)
            total += -o["lado"] * DODGE * w
        return total

    pts, s = [], ds
    while s < LARGO_PISTA - 1.0:
        x, y, psi = pista_en(s)
        x, y = a_mundo(x, y, psi, 0.0, desvio(s))
        pts.append((x, y))
        s += ds
    return pts


RUTA = _ruta_pista()
_x_fin, _y_fin, _psi_fin = pista_en(LARGO_PISTA)
META = (X_TANQUE_ENTREGA + 0.23 + SEP_FILA, _y_fin)   # centro de la zona donde caen los vasos

ESCENARIO = {
    "camino": [list(CL[i]) for i in range(0, len(CL), 5)] + [list(CL[-1])],
    "ancho": ANCHO_CARRETERA,
    "obstaculos": [{"tipo": o["tipo"], "nombre": o["nombre"], "c": [round(o["c"][0], 3), round(o["c"][1], 3)],
                    "yaw": round(o["yaw"], 3), "h": [round(o["h"][0], 3), round(o["h"][1], 3)],
                    "lat": round(o.get("lat", 0.0), 3), "s": round(o["s"], 3)} for o in OBST],
    "ruta": [[round(x, 3), round(y, 3)] for x, y in RUTA],
    "inicio_tanque": list(TANQUE_INICIO),
    "meta": list(META),
    "meta_h": list(META_H),
    "banda": {"y": 0.0, "x_ini": BANDA_X_INI, "x_fin": BANDA_X_FIN_VISUAL,
              "x_tapa": X_TAPA},
}


# ----------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------
def clasificar(diametro_mm):
    """Devuelve la denominación según el primer hueco en el que cabe la moneda."""
    for i, ancho in enumerate(ANCHOS_HUECOS):
        if diametro_mm < ancho:
            return DENOMS[i]
    return DENOMS[-1]


def ruta_entrega(k, total):
    """Ruta del viaje k-ésimo: cada vaso queda en un sitio distinto de la meta."""
    # Casillas fijas (no dependen de cuántos vasos haya): filas de CASILLAS_FILA vasos
    fila, col = divmod(k, CASILLAS_FILA)
    off = (col - (CASILLAS_FILA - 1) / 2) * SEP_CASILLA
    x_stop = X_TANQUE_ENTREGA + SEP_FILA * fila
    return list(RUTA) + [(x_stop - 0.30, META[1] + off), (x_stop, META[1] + off)]


def ruta_regreso(ruta):
    """Vuelve por el mismo camino hasta el punto de inicio del tanque."""
    return list(reversed(ruta[:-1])) + [TANQUE_INICIO]


def norm_ang(a):
    return math.atan2(math.sin(a), math.cos(a))


Q0 = [0, 0, 0, 1]


def crear_caja(half, pos, color, masa=0.0):
    """Caja CON colisión."""
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half)
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=color)
    return p.createMultiBody(masa, col, vis, pos)


def crear_cilindro(radio, alto, pos, color, masa):
    """Cilindro CON colisión."""
    col = p.createCollisionShape(p.GEOM_CYLINDER, radius=radio, height=alto)
    vis = p.createVisualShape(p.GEOM_CYLINDER, radius=radio, length=alto,
                              rgbaColor=color)
    return p.createMultiBody(masa, col, vis, pos)


def deco_caja(half, pos, color, orn=Q0):
    """Caja SOLO visual (adorno)."""
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=color)
    return p.createMultiBody(baseMass=0, baseVisualShapeIndex=vis,
                             basePosition=pos, baseOrientation=orn)


def deco_cil(radio, alto, pos, color, orn=Q0):
    """Cilindro SOLO visual (adorno)."""
    vis = p.createVisualShape(p.GEOM_CYLINDER, radius=radio, length=alto,
                              rgbaColor=color)
    return p.createMultiBody(baseMass=0, baseVisualShapeIndex=vis,
                             basePosition=pos, baseOrientation=orn)


def crear_moneda(denom, pos):
    m = MONEDAS[denom]
    radio = m["d"] / 2 * 1e-3 * ESC_MONEDA
    alto = m["e"] * 1e-3 * ESC_MONEDA
    return crear_cilindro(radio, alto, pos, COLOR_DENOM[denom], m["peso"] / 1000.0)


def crear_vaso(pos, denom):
    """Vaso de vidrio de UNA denominación.

    Tiene N_CAPAS capas de monedas (invisibles al inicio, se revelan al llegar
    cada moneda) y una tapa (invisible hasta que el brazo la coloca).
    """
    col = p.createCollisionShape(p.GEOM_CYLINDER, radius=R_VASO, height=H_VASO)
    vis = p.createVisualShape(p.GEOM_CYLINDER, radius=R_VASO, length=H_VASO,
                              rgbaColor=C_VIDRIO)
    invisible = COLOR_DENOM[denom][:3] + [0.0]
    capa = H_VASO * 0.075
    v_capa = p.createVisualShape(p.GEOM_CYLINDER, radius=R_VASO * 0.88, length=capa,
                                 rgbaColor=invisible)
    v_tapa = p.createVisualShape(p.GEOM_CYLINDER, radius=R_VASO * 1.05, length=0.008,
                                 rgbaColor=invisible)
    n = N_CAPAS + 1
    return p.createMultiBody(
        baseMass=0.2, baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
        basePosition=pos,
        linkMasses=[0.001] * n,
        linkCollisionShapeIndices=[-1] * n,
        linkVisualShapeIndices=[v_capa] * N_CAPAS + [v_tapa],
        linkPositions=[[0, 0, -H_VASO / 2 + (i + 0.5) * capa] for i in range(N_CAPAS)]
                      + [[0, 0, H_VASO / 2 + 0.004]],
        linkOrientations=[Q0] * n,
        linkInertialFramePositions=[[0, 0, 0]] * n,
        linkInertialFrameOrientations=[Q0] * n,
        linkParentIndices=[0] * n,
        linkJointTypes=[p.JOINT_FIXED] * n,
        linkJointAxis=[[0, 0, 1]] * n,
    )


def revelar_capa(vaso, denom, k):
    """Muestra la capa k (0-based) de monedas del vaso."""
    if 0 <= k < N_CAPAS:
        p.changeVisualShape(vaso, k, rgbaColor=COLOR_DENOM[denom])


def poner_tapa_vaso(vaso, denom):
    p.changeVisualShape(vaso, N_CAPAS, rgbaColor=COLOR_DENOM[denom])


# ----------------------------------------------------------------------------
# ESCENA
# ----------------------------------------------------------------------------
_DIR_MALLAS = None


def _obj_cinta(nombre, quads):
    """Escribe una malla .obj (cuadriláteros planos con normal) y devuelve su ruta."""
    global _DIR_MALLAS
    if _DIR_MALLAS is None:
        _DIR_MALLAS = tempfile.mkdtemp(prefix="pista_")
    ruta = os.path.join(_DIR_MALLAS, nombre + ".obj")
    with open(ruta, "w") as f:
        for k, (a, b, c, d) in enumerate(quads):
            for v in (a, b, c, d):
                f.write("v %.5f %.5f %.5f\n" % v)
            f.write("vn 0 0 1\n")
            i = 4 * k + 1
            f.write("f %d//1 %d//1 %d//1\nf %d//1 %d//1 %d//1\n" % (i, i + 1, i + 2, i, i + 2, i + 3))
    return ruta


def _cuadro_pista(s0, s1, lat0, lat1, z):
    """Cuadrilátero entre s0..s1 y lateral lat0..lat1 (+ = izquierda), sobre la línea central."""
    x0, y0, p0 = pista_en(s0)
    x1, y1, p1 = pista_en(s1)
    pts = [a_mundo(x0, y0, p0, 0, lat0), a_mundo(x1, y1, p1, 0, lat0),
           a_mundo(x1, y1, p1, 0, lat1), a_mundo(x0, y0, p0, 0, lat1)]
    return tuple((px, py, z) for px, py in pts)


def deco_malla(ruta, color):
    vis = p.createVisualShape(p.GEOM_MESH, fileName=ruta, rgbaColor=color,
                              meshScale=[1, 1, 1])
    return p.createMultiBody(baseMass=0, baseVisualShapeIndex=vis, basePosition=[0, 0, 0])


def caja_o(half, pos, yaw, color, colision=False):
    """Caja orientada (rumbo yaw), con o sin colisión."""
    orn = p.getQuaternionFromEuler([0, 0, yaw])
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=color)
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half) if colision else -1
    return p.createMultiBody(0, col, vis, pos, orn)


def crear_carretera():
    """Asfalto, bordillos rojo/blanco y línea central discontinua siguiendo la curva."""
    w = ANCHO_CARRETERA / 2
    paso = 0.05
    n = int(LARGO_PISTA / paso)
    asfalto, rojo, blanco, rayas = [], [], [], []
    for k in range(n):
        a, b = k * paso, min(LARGO_PISTA, (k + 1) * paso + 0.004)
        asfalto.append(_cuadro_pista(a, b, -w, w, 0.004))
        franja = (k * paso) // 0.15 % 2 == 0           # bordillos a tramos de 15 cm
        for lado in (+1, -1):
            lat0, lat1 = (w, w + 0.07) if lado > 0 else (-w - 0.07, -w)
            (rojo if franja else blanco).append(_cuadro_pista(a, b, lat0, lat1, 0.0045))
        if (k * paso) // 0.10 % 2 == 0 and k * paso > 0.15 and k * paso < LARGO_PISTA - 0.35:
            rayas.append(_cuadro_pista(a, b, -0.007, 0.007, 0.0055))      # línea central
    deco_malla(_obj_cinta("asfalto", asfalto), C_ASFALTO)
    deco_malla(_obj_cinta("bordillo_rojo", rojo), [0.93, 0.35, 0.30, 1])
    deco_malla(_obj_cinta("bordillo_blanco", blanco), [0.97, 0.97, 0.97, 1])
    deco_malla(_obj_cinta("rayas", rayas), C_LINEA)
    # línea de salida (blanca) y franja de cuadros en la meta
    deco_malla(_obj_cinta("salida", [_cuadro_pista(0.0, 0.05, -w, w, 0.0058)]), C_LINEA)
    cuadros_n, cuadros_b = [], []
    cuad = ANCHO_CARRETERA / 14
    for i in range(14):
        for j in range(3):
            s0 = LARGO_PISTA - 0.30 + j * cuad
            q = _cuadro_pista(s0, s0 + cuad, -w + i * cuad, -w + (i + 1) * cuad, 0.0058)
            (cuadros_n if (i + j) % 2 == 0 else cuadros_b).append(q)
    deco_malla(_obj_cinta("cuadros_n", cuadros_n), [0.08, 0.08, 0.08, 1])
    deco_malla(_obj_cinta("cuadros_b", cuadros_b), [0.97, 0.97, 0.97, 1])


def crear_muro(o):
    """Muro de ladrillo (con colisión) en un lado de la carretera."""
    x, y, psi = o["c"][0], o["c"][1], o["yaw"]
    cx, cy = a_mundo(x, y, psi, 0.0, o["lat"])
    hx, hy, hz = MURO_LARGO / 2, MURO_ANCHO / 2, MURO_ALTO / 2
    idm = caja_o([hx, hy, hz], [cx, cy, hz], psi, C_LADRILLO, colision=True)
    for z in (0.05, 0.10, 0.15):                                     # juntas horizontales
        caja_o([hx + 0.001, hy + 0.001, 0.0025], [cx, cy, z], psi, C_MORTERO)
    for fila, z in enumerate((0.025, 0.075, 0.125, 0.175)):           # juntas verticales
        for lat in (-0.10, 0.0, 0.10):
            lat = lat + (0.05 if fila % 2 else 0.0)
            if abs(lat) < hy - 0.01:
                px, py = a_mundo(cx, cy, psi, 0.0, lat)
                caja_o([hx + 0.001, 0.0025, 0.025], [px, py, z], psi, C_MORTERO)
    return idm


def crear_gravilla(o, semilla=7):
    """Parche de piedras sueltas (cuerpos estáticos compuestos, 3 tonos de gris)."""
    rnd = random.Random(semilla)
    x, y, psi = o["c"][0], o["c"][1], o["yaw"]
    hl, ha = o["h"]
    # base de tierra bajo las piedras
    deco_malla(_obj_cinta("gravilla_base", [_cuadro_pista(o["s"] - hl, o["s"] + hl, -ha, ha, 0.0052)]),
               [0.50, 0.47, 0.43, 1])
    colores = ([0.55, 0.55, 0.57, 1], [0.42, 0.42, 0.45, 1], [0.68, 0.66, 0.62, 1])
    piedras = []
    for _ in range(GRAV_N):
        he = [rnd.uniform(0.008, 0.016), rnd.uniform(0.008, 0.016), rnd.uniform(0.006, 0.014)]
        px, py = a_mundo(x, y, psi, rnd.uniform(-hl, hl), rnd.uniform(-ha, ha))
        orn = p.getQuaternionFromEuler([rnd.uniform(-0.5, 0.5), rnd.uniform(-0.5, 0.5),
                                        rnd.uniform(0, math.pi)])
        piedras.append((he, [px, py, he[2] * 0.6], orn, colores[rnd.randrange(3)]))
    n = len(piedras)
    cols = [p.createCollisionShape(p.GEOM_BOX, halfExtents=e[0]) for e in piedras]
    viss = [p.createVisualShape(p.GEOM_BOX, halfExtents=e[0], rgbaColor=e[3]) for e in piedras]
    # una sola estructura estática: cada piedra es un enlace fijo
    p.createMultiBody(
        baseMass=0, baseCollisionShapeIndex=-1, baseVisualShapeIndex=-1, basePosition=[0, 0, 0],
        linkMasses=[0.01] * n, linkCollisionShapeIndices=cols, linkVisualShapeIndices=viss,
        linkPositions=[e[1] for e in piedras], linkOrientations=[e[2] for e in piedras],
        linkInertialFramePositions=[[0, 0, 0]] * n, linkInertialFrameOrientations=[Q0] * n,
        linkParentIndices=[0] * n, linkJointTypes=[p.JOINT_FIXED] * n,
        linkJointAxis=[[0, 0, 1]] * n)


def crear_escalera(o):
    """Escalera: ESC_N subidas, plataforma y ESC_N bajadas (cajas sólidas con borde amarillo)."""
    x, y, psi = o["c"][0], o["c"][1], o["yaw"]
    hl = o["largo"] / 2
    gris, amarillo = [0.74, 0.74, 0.72, 1], [0.95, 0.78, 0.15, 1]
    # (inicio longitudinal, largo, alto) de cada bloque
    bloques, lon = [], -hl
    for i in range(ESC_N):
        bloques.append((lon, ESC_HUELLA, (i + 1) * ESC_ALTO)); lon += ESC_HUELLA
    bloques.append((lon, ESC_PLAT, ESC_N * ESC_ALTO)); lon += ESC_PLAT
    for i in range(ESC_N):
        alto = (ESC_N - 1 - i) * ESC_ALTO
        if alto > 0:
            bloques.append((lon, ESC_HUELLA, alto))
        lon += ESC_HUELLA
    for ini, largo, alto in bloques:
        px, py = a_mundo(x, y, psi, ini + largo / 2, 0.0)
        caja_o([largo / 2, ESC_ANCHO / 2, alto / 2], [px, py, alto / 2], psi, gris, colision=True)
        for borde in (ini + 0.008, ini + largo - 0.008):                 # franjas amarillas
            ex, ey = a_mundo(x, y, psi, borde, 0.0)
            caja_o([0.006, ESC_ANCHO / 2, 0.0008], [ex, ey, alto + 0.0008], psi, amarillo)
    # barandillas laterales bajas (solo visuales)
    for lat in (-ESC_ANCHO / 2 - 0.012, ESC_ANCHO / 2 + 0.012):
        px, py = a_mundo(x, y, psi, 0.0, lat)
        caja_o([hl, 0.008, ESC_N * ESC_ALTO / 2], [px, py, ESC_N * ESC_ALTO / 2],
               psi, [0.55, 0.57, 0.60, 1])


def crear_separador():
    """Separador de monedas montado SOBRE la banda (alineado con ella)."""
    # Carcasa superior con las 5 bocas y tubos guía hasta casi tocar los vasos
    deco_caja([0.50, 0.09, 0.09], [0, 0, 0.78], [0.92, 0.94, 0.97, 1])
    deco_caja([0.20, 0.092, 0.012], [0, 0, 0.84], C_TANQUE)               # placa título
    for sx in (-0.47, 0.47):                                             # columnas
        for sy in (-0.125, 0.125):
            deco_caja([0.012, 0.012, 0.34], [sx, sy, 0.34], C_ACERO)
    largo_tubo = 0.69 - 0.16
    for x, d in zip(X_CANALES, DENOMS):
        deco_cil(0.05, largo_tubo, [x, 0, 0.16 + largo_tubo / 2],
                 COLOR_DENOM[d][:3] + [0.20])                            # tubo guía
        for dx, dy, hx, hy in ((0.06, 0, 0.004, 0.06), (-0.06, 0, 0.004, 0.06),
                               (0, 0.06, 0.06, 0.004), (0, -0.06, 0.06, 0.004)):
            deco_caja([hx, hy, 0.004], [x + dx, dy, Z_SENSOR], [0.95, 0.15, 0.15, 1])
        deco_caja([0.045, 0.07, 0.0008], [x, 0, Z_TOPE_BANDA + 0.0016],
                  COLOR_DENOM[d])                                         # marca en banda


def crear_banda():
    crear_caja(BANDA_MEDIO, BANDA_CENTRO, C_OSCURO)
    largo = BANDA_MEDIO[0]
    for sy in (-0.088, 0.088):                                            # barandas
        deco_caja([largo, 0.006, 0.02], [BANDA_CENTRO[0], sy, 0.07], C_ACERO)
    rod = p.getQuaternionFromEuler([math.pi / 2, 0, 0])
    for xr in (BANDA_X_INI, BANDA_X_FIN_VISUAL):                          # rodillos
        deco_cil(0.034, 0.17, [xr, 0.0, 0.03], C_ACERO, rod)
    n_patas = int((BANDA_X_FIN_VISUAL - BANDA_X_INI) / 0.65) + 1
    for i in range(n_patas):                                              # patas
        xl = BANDA_X_INI + 0.05 + i * (BANDA_X_FIN_VISUAL - BANDA_X_INI - 0.1) / (n_patas - 1)
        for sy in (-0.07, 0.07):
            deco_caja([0.012, 0.012, 0.015], [xl, sy, 0.015], C_ACERO)
    n_marcas = int((BANDA_X_FIN_VISUAL - BANDA_X_INI) / 0.1)
    for k in range(n_marcas):                                             # marcas
        deco_caja([0.004, 0.07, 0.0008], [BANDA_X_INI + 0.05 + 0.1 * k, 0.0,
                                          Z_TOPE_BANDA + 0.001], [0.28, 0.29, 0.33, 1])


def crear_almacen_tapas():
    """Pedestal con una pila de tapas, de donde las toma el brazo."""
    deco_caja([0.04, 0.04, 0.065], [ALMACEN[0], ALMACEN[1], 0.065], C_ACERO)
    for i in range(3):
        deco_cil(0.033, 0.0045, [ALMACEN[0], ALMACEN[1], 0.1325 + 0.0047 * i],
                 [0.90, 0.90, 0.93, 1])


def crear_brazo():
    """Brazo de tapas: base gris, columna turquesa, brazo naranja, muñeca rosa y
    pinza blanca (mismos colores que el brazo del modelo base). Se mueve por
    articulaciones: giro (yaw), bajada (slide) y dos dedos de pinza."""
    L = L_BRAZO
    x, y = BRAZO_BASE
    gris, turq = [0.45, 0.45, 0.47, 1], [0.29, 0.56, 0.63, 1]
    naranja, rosa, blanco = [0.72, 0.50, 0.20, 1], [0.95, 0.52, 0.52, 1], [0.96, 0.96, 0.96, 1]

    col_b = p.createCollisionShape(p.GEOM_CYLINDER, radius=0.07, height=0.03)
    vis_b = p.createVisualShape(p.GEOM_CYLINDER, radius=0.07, length=0.03, rgbaColor=gris)
    col0 = p.createCollisionShape(p.GEOM_CYLINDER, radius=0.03, height=0.20)
    vis0 = p.createVisualShape(p.GEOM_CYLINDER, radius=0.03, length=0.20, rgbaColor=turq)
    col1 = p.createCollisionShape(p.GEOM_BOX, halfExtents=[L / 2, 0.012, 0.012],
                                  collisionFramePosition=[L / 2, 0, 0.012])
    vis1 = p.createVisualShape(p.GEOM_BOX, halfExtents=[L / 2, 0.012, 0.012],
                               rgbaColor=naranja, visualFramePosition=[L / 2, 0, 0.012])
    col2 = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.015, 0.015, 0.02],
                                  collisionFramePosition=[0, 0, -0.02])
    vis2 = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.015, 0.015, 0.02],
                               rgbaColor=rosa, visualFramePosition=[0, 0, -0.02])
    col_f = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.005, 0.004, 0.015])
    vis_f = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.005, 0.004, 0.015],
                                rgbaColor=blanco)
    vis_t = p.createVisualShape(p.GEOM_CYLINDER, radius=0.033, length=0.006,
                                rgbaColor=[1, 1, 1, 0.0])

    brazo = p.createMultiBody(
        baseMass=0.0, baseCollisionShapeIndex=col_b, baseVisualShapeIndex=vis_b,
        basePosition=[x, y, 0.015],
        linkMasses=[0.5, 0.3, 0.1, 0.02, 0.02, 0.001],
        linkCollisionShapeIndices=[col0, col1, col2, col_f, col_f, -1],
        linkVisualShapeIndices=[vis0, vis1, vis2, vis_f, vis_f, vis_t],
        linkPositions=[[0, 0, 0.115], [0, 0, 0.10], [L, 0, 0],
                       [0, 0.04, -0.06], [0, -0.04, -0.06], [0, 0, -0.07]],
        linkOrientations=[Q0] * 6,
        linkInertialFramePositions=[[0, 0, 0]] * 6,
        linkInertialFrameOrientations=[Q0] * 6,
        linkParentIndices=[0, 1, 2, 3, 3, 3],
        linkJointTypes=[p.JOINT_FIXED, p.JOINT_REVOLUTE, p.JOINT_PRISMATIC,
                        p.JOINT_PRISMATIC, p.JOINT_PRISMATIC, p.JOINT_FIXED],
        linkJointAxis=[[0, 0, 1], [0, 0, 1], [0, 0, -1], [0, -1, 0], [0, 1, 0], [0, 0, 1]],
    )
    for j in range(-1, 6):                      # el brazo no choca con nada
        p.setCollisionFilterGroupMask(brazo, j, 0, 0)
    p.changeDynamics(brazo, J_SLIDE, jointLowerLimit=0.0, jointUpperLimit=0.03)
    for j in (J_DEDO_I, J_DEDO_D):
        p.changeDynamics(brazo, j, jointLowerLimit=0.0, jointUpperLimit=0.006)
    p.resetJointState(brazo, J_YAW, YAW_ALMACEN)
    brazo_mover(brazo, yaw=YAW_ALMACEN, bajar=0.0, cerrar=False)
    return brazo


def crear_escena():
    plano = p.loadURDF("plane.urdf")
    p.changeVisualShape(plano, -1, textureUniqueId=-1, rgbaColor=C_PISO)

    crear_separador()
    crear_banda()
    crear_almacen_tapas()

    # ---------- Zonas: carga y meta ----------
    deco_caja([0.30, 0.24, 0.0015], [1.60 + DX, 0.0, 0.0025], [0.60, 0.78, 0.94, 1])
    deco_caja([META_H[0], META_H[1], 0.0015], [META[0], META[1], 0.0070], C_META)

    # ---------- Carretera y obstáculos ----------
    crear_carretera()
    ids_muros = []
    for o in OBST:
        if o["tipo"] == "muro":
            ids_muros.append(crear_muro(o))
        elif o["tipo"] == "gravilla":
            crear_gravilla(o)
        else:
            crear_escalera(o)

    # ---------- Meta: pórtico con bandera a cuadros ----------
    xm, ym = META[0] + META_H[0] + 0.16, META[1]
    for lat in (-ANCHO_CARRETERA / 2 - 0.10, ANCHO_CARRETERA / 2 + 0.10):
        deco_caja([0.012, 0.012, 0.30], [xm, ym + lat, 0.30], [0.2, 0.2, 0.2, 1])
    for i in range(14):
        for j in range(3):
            negro = (i + j) % 2 == 0
            deco_caja([0.004, 0.0275, 0.0275],
                      [xm, ym - ANCHO_CARRETERA / 2 - 0.10 + 0.0275 + 0.055 * i
                       + (ANCHO_CARRETERA + 0.2 - 14 * 0.055) / 2, 0.60 - 0.0275 - 0.055 * j],
                      [0.08, 0.08, 0.08, 1] if negro else [0.97, 0.97, 0.97, 1])
    return ids_muros


def crear_tanque(pos_xy, yaw):
    """Tanque de orugas con brazo articulado y pinza de dos dedos (3 servos).

    Servo 1 = hombro del brazo (sube / baja)        -> articulación 6
    (muñeca)  = articulación 7, acoplada en paralelo al hombro para que la pinza
                quede siempre derecha (como un paralelogramo; no es un servo)
    Servo 2 = dedo izquierdo (abre / cierra)        -> articulación 8
    Servo 3 = dedo derecho  (abre / cierra)         -> articulación 9
    """
    Z_P = 0.09                                   # altura del hombro sobre el centro del chasis
    X_P = 0.04                                   # posición del hombro (x)
    col_base = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.14, 0.07, 0.03])
    vis_base = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.14, 0.07, 0.03],
                                   rgbaColor=C_TANQUE)
    col_r = p.createCollisionShape(p.GEOM_CYLINDER, radius=R_RUEDA, height=0.03)
    vis_r = p.createVisualShape(p.GEOM_CYLINDER, radius=R_RUEDA, length=0.03,
                                rgbaColor=C_OSCURO)
    # brazo: barra naranja del hombro a la muñeca
    col_br = p.createCollisionShape(p.GEOM_BOX, halfExtents=[L_BRAZO / 2, 0.012, 0.012],
                                    collisionFramePosition=[L_BRAZO / 2, 0, 0])
    vis_br = p.createVisualShape(p.GEOM_BOX, halfExtents=[L_BRAZO / 2, 0.012, 0.012],
                                 rgbaColor=C_ACENTO, visualFramePosition=[L_BRAZO / 2, 0, 0])
    # palma de la pinza (barra negra horizontal)
    col_pa = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.012, Y_DEDO + 0.008, 0.008],
                                    collisionFramePosition=[OFF_PALMA, 0, 0])
    vis_pa = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.012, Y_DEDO + 0.008, 0.008],
                                 rgbaColor=[0.10, 0.10, 0.12, 1],
                                 visualFramePosition=[OFF_PALMA, 0, 0])
    # dedos: placas que cuelgan de la palma y giran hacia adentro / afuera
    col_d = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.012, 0.004, L_DEDO / 2],
                                   collisionFramePosition=[0, 0, -L_DEDO / 2])
    vis_d = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.012, 0.004, L_DEDO / 2],
                                rgbaColor=[0.72, 0.74, 0.78, 1],
                                visualFramePosition=[0, 0, -L_DEDO / 2])

    q_rueda = p.getQuaternionFromEuler([-math.pi / 2, 0, 0])   # eje z -> eje +y
    y_o = SEP_ORUGAS / 2
    pos_ruedas = [(x, y, -Z_RUEDA) for y in (+y_o, -y_o) for x in (-0.1, 0.0, 0.1)]

    # Adornos fijos al chasis: orugas, cabina, luz y torre del hombro
    v_oruga = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.10, 0.017, 0.0425],
                                  rgbaColor=[0.07, 0.08, 0.09, 1])
    v_extremo = p.createVisualShape(p.GEOM_CYLINDER, radius=0.0425, length=0.034,
                                    rgbaColor=[0.07, 0.08, 0.09, 1])
    v_cabina = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.07, 0.055, 0.03],
                                   rgbaColor=[0.95, 0.96, 0.98, 1])
    v_luz = p.createVisualShape(p.GEOM_SPHERE, radius=0.012, rgbaColor=[0.95, 0.15, 0.15, 1])
    v_torre = p.createVisualShape(p.GEOM_CYLINDER, radius=0.032, length=0.05,
                                  rgbaColor=[0.08, 0.08, 0.10, 1])
    decor = []
    for sgn in (+1, -1):
        decor.append((v_oruga, (0, sgn * y_o, -Z_RUEDA), Q0))
        for xe in (-0.1, 0.1):
            decor.append((v_extremo, (xe, sgn * y_o, -Z_RUEDA), q_rueda))
    decor += [(v_cabina, (-0.07, 0, 0.055), Q0),
              (v_luz, (-0.07, 0, 0.10), Q0),
              (v_torre, (X_P, 0, 0.055), Q0)]            # torre donde gira el hombro

    # Adornos que se mueven con el brazo: servo del hombro y servo de la muñeca (azules)
    v_servo = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.022, 0.022, 0.022],
                                  rgbaColor=[0.10, 0.30, 0.80, 1])
    v_servo_m = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.016, 0.018, 0.016],
                                    rgbaColor=[0.10, 0.30, 0.80, 1])
    decor_brazo = [(v_servo, (0.0, 0.0, 0.0), Q0), (v_servo_m, (L_BRAZO, 0.0, 0.0), Q0)]
    # Adornos de la palma: servos de los dedos
    v_servo_d = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.014, 0.012, 0.012],
                                    rgbaColor=[0.10, 0.30, 0.80, 1])
    v_conector = p.createVisualShape(p.GEOM_BOX, halfExtents=[OFF_PALMA / 2, 0.01, 0.008],
                                     rgbaColor=[0.10, 0.10, 0.12, 1])
    decor_palma = [(v_servo_d, (OFF_PALMA, +Y_DEDO, 0.012), Q0),
                   (v_servo_d, (OFF_PALMA, -Y_DEDO, 0.012), Q0),
                   (v_conector, (OFF_PALMA / 2, 0.0, 0.0), Q0)]

    n_da, n_dp, n_d = len(decor_brazo), len(decor_palma), len(decor)
    n_extra = n_da + n_dp + n_d
    P_BRAZO, P_MUNECA = J_BRAZO_T + 1, J_MUNECA_T + 1      # índice de padre = link + 1
    tanque = p.createMultiBody(
        baseMass=2.0,
        baseCollisionShapeIndex=col_base,
        baseVisualShapeIndex=vis_base,
        basePosition=[pos_xy[0], pos_xy[1], R_RUEDA + Z_RUEDA],
        baseOrientation=p.getQuaternionFromEuler([0, 0, yaw]),
        linkMasses=[0.1] * 6 + [0.15, 0.05, 0.03, 0.03] + [0.001] * n_extra,
        linkCollisionShapeIndices=[col_r] * 6 + [col_br, col_pa, col_d, col_d] + [-1] * n_extra,
        linkVisualShapeIndices=[vis_r] * 6 + [vis_br, vis_pa, vis_d, vis_d]
                               + [d[0] for d in decor_brazo] + [d[0] for d in decor_palma]
                               + [d[0] for d in decor],
        linkPositions=pos_ruedas + [(X_P, 0, Z_P), (L_BRAZO, 0, 0),
                                    (OFF_PALMA, +Y_DEDO, -0.008), (OFF_PALMA, -Y_DEDO, -0.008)]
                      + [d[1] for d in decor_brazo] + [d[1] for d in decor_palma]
                      + [d[1] for d in decor],
        linkOrientations=[q_rueda] * 6 + [Q0] * 4
                         + [d[2] for d in decor_brazo] + [d[2] for d in decor_palma]
                         + [d[2] for d in decor],
        linkInertialFramePositions=[[0, 0, 0]] * (10 + n_extra),
        linkInertialFrameOrientations=[Q0] * (10 + n_extra),
        linkParentIndices=[0] * 7 + [P_BRAZO] + [P_MUNECA] * 2 + [P_BRAZO] * n_da
                          + [P_MUNECA] * n_dp + [0] * n_d,
        linkJointTypes=[p.JOINT_REVOLUTE] * 10 + [p.JOINT_FIXED] * n_extra,
        linkJointAxis=[[0, 0, 1]] * 6 + [[0, -1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0]]
                      + [[0, 0, 1]] * n_extra,
    )
    for j in range(6):
        p.changeDynamics(tanque, j, lateralFriction=3.0,
                         anisotropicFriction=[1.0, 1.0, 0.05])
    # Orugas de caucho: mucho agarre al rodar (3.0 permite subir escalones) y poco de lado
    # (3.0 x 0.05 = 0.15): el tanque gira en el sitio de forma pareja en cualquier rumbo.
    p.changeDynamics(tanque, J_BRAZO_T, jointLowerLimit=-0.05, jointUpperLimit=BRAZO_T_ARRIBA + 0.1)
    p.changeDynamics(tanque, J_MUNECA_T, jointLowerLimit=-BRAZO_T_ARRIBA - 0.2,
                     jointUpperLimit=0.2)
    for j in (J_DEDO_T_I, J_DEDO_T_D):
        p.changeDynamics(tanque, j, lateralFriction=1.0,
                         jointLowerLimit=-DEDO_LIM, jointUpperLimit=DEDO_LIM)
    # posición de reposo: brazo arriba, muñeca compensada y pinza abierta
    p.resetJointState(tanque, J_BRAZO_T, BRAZO_T_ARRIBA)
    p.resetJointState(tanque, J_MUNECA_T, -BRAZO_T_ARRIBA)
    for j in (J_DEDO_T_I, J_DEDO_T_D):
        p.resetJointState(tanque, j, DEDO_ABIERTO)
    brazo_t(tanque, arriba=True)
    pinza(tanque, cerrada=False)
    muneca_paralela(tanque)
    return tanque


# ----------------------------------------------------------------------------
# CONTROL DEL BRAZO DE TAPAS
# ----------------------------------------------------------------------------
def brazo_mover(brazo, yaw=None, bajar=None, cerrar=None):
    if yaw is not None:
        p.setJointMotorControl2(brazo, J_YAW, p.POSITION_CONTROL, targetPosition=yaw,
                                force=30, maxVelocity=1.8)
    if bajar is not None:
        p.setJointMotorControl2(brazo, J_SLIDE, p.POSITION_CONTROL, targetPosition=bajar,
                                force=30, maxVelocity=0.12)
    if cerrar is not None:
        for j in (J_DEDO_I, J_DEDO_D):
            p.setJointMotorControl2(brazo, j, p.POSITION_CONTROL,
                                    targetPosition=0.006 if cerrar else 0.0,
                                    force=10, maxVelocity=0.05)


def brazo_alcanzo(brazo, yaw=None, bajar=None, cerrar=None):
    ok = True
    if yaw is not None:
        ok &= abs(norm_ang(p.getJointState(brazo, J_YAW)[0] - yaw)) < 0.03
    if bajar is not None:
        ok &= abs(p.getJointState(brazo, J_SLIDE)[0] - bajar) < 0.002
    if cerrar is not None:
        objetivo = 0.006 if cerrar else 0.0
        ok &= abs(p.getJointState(brazo, J_DEDO_I)[0] - objetivo) < 0.0015
    return ok


def tapa_en_brazo(brazo, visible, denom=None):
    color = COLOR_DENOM[denom][:3] + [1.0] if visible else [1, 1, 1, 0.0]
    p.changeVisualShape(brazo, LINK_TAPA_BRAZO, rgbaColor=color)


# ----------------------------------------------------------------------------
# CONTROL DEL TANQUE
# ----------------------------------------------------------------------------
def pose2d(cuerpo):
    pos, orn = p.getBasePositionAndOrientation(cuerpo)
    return pos[0], pos[1], p.getEulerFromQuaternion(orn)[2]


def mover(tanque, v, w):
    """v (m/s) avance, w (rad/s) giro antihorario. Diferencial de orugas."""
    vl = v - w * SEP_ORUGAS / 2
    vr = v + w * SEP_ORUGAS / 2
    for j in range(3):                 # orugas izquierdas (+y)
        p.setJointMotorControl2(tanque, j, p.VELOCITY_CONTROL,
                                targetVelocity=vl / R_RUEDA, force=FUERZA_RUEDA)
    for j in range(3, 6):              # orugas derechas (-y)
        p.setJointMotorControl2(tanque, j, p.VELOCITY_CONTROL,
                                targetVelocity=vr / R_RUEDA, force=FUERZA_RUEDA)


def brazo_t(tanque, arriba):
    """Servo 1: sube o baja el brazo girando en el hombro."""
    objetivo = BRAZO_T_ARRIBA if arriba else 0.0
    p.setJointMotorControl2(tanque, J_BRAZO_T, p.POSITION_CONTROL,
                            targetPosition=objetivo, force=12, maxVelocity=1.0)


def brazo_t_listo(tanque, arriba):
    objetivo = BRAZO_T_ARRIBA if arriba else 0.0
    return abs(p.getJointState(tanque, J_BRAZO_T)[0] - objetivo) < 0.04


def muneca_paralela(tanque):
    """La muñeca gira lo contrario al hombro (paralelogramo): la pinza siempre queda derecha
    y el vaso no se inclina. Se llama en cada paso de la simulación."""
    ang = p.getJointState(tanque, J_BRAZO_T)[0]
    p.setJointMotorControl2(tanque, J_MUNECA_T, p.POSITION_CONTROL,
                            targetPosition=-ang, force=6, maxVelocity=3.0)


def pinza(tanque, cerrada):
    """Servos 2 y 3: cada uno mueve un dedo (cerrar / abrir)."""
    objetivo = DEDO_CERRADO if cerrada else DEDO_ABIERTO
    for j in (J_DEDO_T_I, J_DEDO_T_D):
        p.setJointMotorControl2(tanque, j, p.POSITION_CONTROL,
                                targetPosition=objetivo, force=2.0, maxVelocity=1.5)


def pinza_abierta(tanque):
    return all(abs(p.getJointState(tanque, j)[0] - DEDO_ABIERTO) < 0.05
               for j in (J_DEDO_T_I, J_DEDO_T_D))


_ATASCO = {"n": 0, "yaw": 0.0, "dir": 0, "m": 0, "pose": (0.0, 0.0, 0.0), "rev": 0,
           "obj": None, "dmin": 9.9}


def _vigilar_atasco(tanque):
    """Si el tanque casi no se movió en 1.5 s mientras se le ordena avanzar o girar,
    retrocede 0.6 s para soltar el contacto pegado. Devuelve True si está retrocediendo."""
    a = _ATASCO
    if a["rev"] > 0:
        a["rev"] -= 1
        mover(tanque, -0.15, 0)
        return True
    x, y, yaw = pose2d(tanque)
    if a["m"] == 0:
        a["pose"] = (x, y, yaw)
    a["m"] += 1
    if a["m"] >= 360:
        x0, y0, yaw0 = a["pose"]
        movido = math.hypot(x - x0, y - y0) + 0.15 * abs(norm_ang(yaw - yaw0))
        if movido < 0.04:
            a["rev"] = 150
        a["m"] = 0
    return False


def rotar_en_sitio(tanque, w, err):
    """Gira en el sitio hacia el rumbo deseado (err = error de rumbo, rad).

    La física de las ruedas tiene un rumbo (cerca de yaw = 0) donde el giro se atasca.
    Si en 0.75 s el rumbo casi no cambió, se fuerza el giro por el lado contrario
    (la vuelta larga) hasta que el error de rumbo sea pequeño."""
    a = _ATASCO
    if a["dir"] != 0:
        if abs(err) < 0.6:
            a["dir"] = 0
        else:
            w = a["dir"] * max(abs(w), 1.2)
    yaw = pose2d(tanque)[2]
    if a["n"] == 0:
        a["yaw"] = yaw
    a["n"] += 1
    if a["n"] >= 180:
        if a["dir"] == 0 and abs(norm_ang(yaw - a["yaw"])) < 0.12:
            a["dir"] = -1 if w > 0 else 1
        a["n"] = 0
    mover(tanque, 0, w)


def ir_a(tanque, objetivo, tol=0.05):
    x, y, yaw = pose2d(tanque)
    dx, dy = objetivo[0] - x, objetivo[1] - y
    dist = math.hypot(dx, dy)
    a = _ATASCO
    if a["obj"] != tuple(objetivo):                 # punto nuevo: reiniciar la distancia mínima
        a["obj"], a["dmin"] = tuple(objetivo), dist
    a["dmin"] = min(a["dmin"], dist)
    # Se da por alcanzado si está dentro de la tolerancia o, en puntos intermedios (tol >= 0.06),
    # si ya pasó muy cerca y empieza a alejarse (evita dar vueltas alrededor del punto).
    paso_cerca = tol >= 0.06 and a["dmin"] < 0.14 and dist > a["dmin"] + 0.04
    if dist < tol or paso_cerca:
        mover(tanque, 0, 0)
        a.update(n=0, dir=0, m=0, rev=0, obj=None, dmin=9.9)
        return True
    if _vigilar_atasco(tanque):
        return False
    err = norm_ang(math.atan2(dy, dx) - yaw)
    w = max(-W_MAX, min(W_MAX, 2.5 * err))
    # Cerca del punto solo se avanza con el rumbo bien alineado; si no, el tanque daría
    # vueltas en círculo alrededor del punto sin poder llegar.
    lim_err = 0.6 if dist > 0.25 else 0.25
    if abs(err) < lim_err:
        v = min(V_MAX, 1.5 * dist) * max(0.0, math.cos(err)) ** 2
        _ATASCO.update(n=0, dir=0)
        mover(tanque, v, w)
    else:
        rotar_en_sitio(tanque, w, err)
    return False


def orientar(tanque, yaw_obj, tol=0.05):
    err = norm_ang(yaw_obj - pose2d(tanque)[2])
    if abs(err) < tol:
        mover(tanque, 0, 0)
        _ATASCO.update(n=0, dir=0)
        return True
    rotar_en_sitio(tanque, max(-W_MAX, min(W_MAX, 2.0 * err)), err)
    return False


def sujetar(tanque, vaso):
    """Une el vaso a la palma de la pinza: el vaso sube y baja con el servo 1."""
    ls = p.getLinkState(tanque, J_MUNECA_T, computeForwardKinematics=1)
    pos_l, orn_l = ls[0], ls[1]            # marco de la palma (inercia en el origen)
    pos_v, orn_v = p.getBasePositionAndOrientation(vaso)
    inv_p, inv_o = p.invertTransform(pos_l, orn_l)
    rel_p, rel_o = p.multiplyTransforms(inv_p, inv_o, pos_v, orn_v)
    return p.createConstraint(tanque, J_MUNECA_T, vaso, -1, p.JOINT_FIXED, [0, 0, 0],
                              rel_p, [0, 0, 0], rel_o, Q0)


# ----------------------------------------------------------------------------
# TEXTOS EN PANTALLA
# ----------------------------------------------------------------------------
def texto(txt, pos, size=1.2, color=(0.1, 0.15, 0.3), reemplazar=-1):
    return p.addUserDebugText(txt, pos, textColorRGB=list(color), textSize=size,
                              replaceItemUniqueId=reemplazar)


def fmt_pesos(v):
    return "$" + f"{v:,}".replace(",", ".")


NOMBRES_ESTADO = {
    "ESPERANDO_VASO": "esperando vaso", "ACERCARSE": "acercandose al vaso",
    "ALINEAR": "alineandose", "BAJAR_BRAZO": "bajando brazo",
    "AGARRAR": "cerrando pinza", "SUBIR_BRAZO": "subiendo vaso",
    "EN_RUTA": "en ruta", "BAJAR_ENTREGA": "bajando vaso",
    "SOLTAR": "abriendo pinza", "SUBIR_VACIO": "subiendo brazo",
    "REGRESAR": "regresando",
    "FIN": "meta alcanzada",
}


def pos_x(cuerpo):
    return p.getBasePositionAndOrientation(cuerpo)[0][0]


# ----------------------------------------------------------------------------
# PRINCIPAL
# ----------------------------------------------------------------------------
def escribir_estado(estado):
    tmp = ARCHIVO_ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ARCHIVO_ESTADO)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-gui", action="store_true")
    ap.add_argument("--monedas", type=int, default=20, help="monedas a procesar")
    ap.add_argument("--cada", type=float, default=0.8, help="s entre monedas")
    ap.add_argument("--max-tiempo", type=float, default=900.0, help="s simulados")
    ap.add_argument("--semilla", type=int, default=None)
    ap.add_argument("--traza", action="store_true",
                    help="imprime cada cambio de estado del tanque (depuración)")
    ap.add_argument("--velocidad", type=float, default=1.0,
                    help="factor de velocidad de la ventana (2 = el doble de rápido)")
    args = ap.parse_args()
    if args.semilla is not None:
        random.seed(args.semilla)

    p.connect(p.DIRECT if args.sin_gui else p.GUI)
    p.setAdditionalSearchPath(pybullet_data.getDataPath())
    p.setGravity(0, 0, -9.81)
    p.setTimeStep(DT)
    if not args.sin_gui:
        p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
        p.configureDebugVisualizer(p.COV_ENABLE_SHADOWS, 1)
        p.resetDebugVisualizerCamera(7.0, 25, -48, [3.0, 1.6, 0.0])

    muros = crear_escena()
    brazo = crear_brazo()
    tanque = crear_tanque(TANQUE_INICIO, PICK_YAW)

    # Un vaso por denominación, debajo de su canal y sobre la banda
    vasos = []
    vaso_de = {}
    for x, d in zip(X_CANALES, DENOMS):
        vid = crear_vaso([x, 0.0, Z_TOPE_BANDA + H_VASO / 2 + 0.001], d)
        v = {"id": vid, "denom": d, "n": 0, "valor": 0, "estado": "LLENANDO",
             "tapado": False, "listo": False, "en_estacion": False,
             "tomado": False, "entregado": False}
        vasos.append(v)
        vaso_de[d] = v

    # Textos fijos y dinámicos
    texto("SISTEMA DE LOGISTICA DE MONEDAS INTELIGENTES", [-0.45, 0, 1.00],
          size=1.5, color=(0.05, 0.25, 0.6))
    texto("Banda transportadora", [0.45 + DX / 2, -0.16, 0.17], size=1.1)
    texto("Brazo de tapas", [X_TAPA - 0.1, BRAZO_BASE[1], 0.34], size=1.1,
          color=(0.55, 0.35, 0.1))
    texto("Meta", [META[0] + META_H[0] + 0.10, META[1] - 0.05, 0.66], size=1.4,
          color=(0.1, 0.5, 0.2))
    texto("Inicio de pista", [PICK_POS[0] + 0.15, -0.55, 0.30], size=1.4, color=(0.1, 0.15, 0.3))
    for k, o in enumerate(OBST, 1):
        alto = {"muro": 0.30, "gravilla": 0.20, "escalera": 0.25}[o["tipo"]]
        texto(f"Obstaculo {k}: {o['nombre']}", [o["c"][0] - 0.12, o["c"][1] + 0.0, alto],
              size=1.0, color=(0.55, 0.15, 0.1))
    ids_canal = {}
    for x, d in zip(X_CANALES, DENOMS):
        ids_canal[d] = texto(f"${d}: 0", [x - 0.05, -0.16, 0.22], size=1.0,
                             color=tuple(c * 0.55 for c in COLOR_DENOM[d][:3]))
    id_hud = texto("Total: $0 | Monedas: 0 | Vasos: 0/0 entregados", [-0.45, 0, 0.93],
                   size=1.2, color=(0.1, 0.1, 0.1))
    id_tanque = texto("Tanque: esperando vaso", [1.6 + DX, -0.45, 0.25], size=1.1,
                      color=(0.1, 0.3, 0.7))
    ultimo_texto = None

    # Estado del sistema
    cuentas = {d: 0 for d in DENOMS}
    reales = {d: 0 for d in DENOMS}      # lo que realmente se soltó (precisión)
    monedas_vivas = {}                   # id -> {"canal","contada","quitada"}
    spawn_hechas = 0
    t_prox_spawn = 0.5
    t_ultima_moneda = 0.0
    embalaje_hecho = False
    total_vasos = 0
    entregados = 0
    tapadas = 0

    # Brazo de tapas (máquina de estados)
    brazo_estado = "REPOSO"
    vaso_brazo = None
    t_brazo = 0.0

    # Tanque (máquina de estados)
    estado_tanque = "ESPERANDO_VASO"
    estado_previo = None
    vaso_tanque = None
    constraint = None
    idx_ruta = 0
    ruta_actual = []
    ruta_vuelta = []
    t_estado = 0.0
    colisiones = 0
    en_contacto = [False] * len(muros)
    meta_ok = False
    trayectoria = []
    serie = []

    t = 0.0
    t_json = 0.0
    t_fin = None

    print("Simulación iniciada. Escribiendo", ARCHIVO_ESTADO.resolve())
    try:
        while t < args.max_tiempo:
            # ---------------- 1) Separador: soltar, contar y llenar vasos ----------------
            if spawn_hechas < args.monedas and t >= t_prox_spawn:
                denom = random.choice(DENOMS)
                d_medido = MONEDAS[denom]["d"] + random.uniform(-TOLERANCIA_MM,
                                                                TOLERANCIA_MM)
                canal = clasificar(d_medido)
                x = X_CANALES[DENOMS.index(canal)] + random.uniform(-0.008, 0.008)
                cid = crear_moneda(denom, [x, 0.0, Z_SPAWN])
                monedas_vivas[cid] = {"canal": canal, "contada": False, "quitada": False}
                reales[denom] += 1
                spawn_hechas += 1
                t_prox_spawn = t + args.cada

            for cid, info in monedas_vivas.items():
                if info["quitada"]:
                    continue
                z = p.getBasePositionAndOrientation(cid)[0][2]
                if not info["contada"] and z < Z_SENSOR:
                    info["contada"] = True
                    cuentas[info["canal"]] += 1
                if z < Z_ENTRADA_VASO:          # la moneda cae dentro del vaso
                    if not info["contada"]:
                        info["contada"] = True
                        cuentas[info["canal"]] += 1
                    v = vaso_de[info["canal"]]
                    v["n"] += 1
                    v["valor"] = v["denom"] * v["n"]
                    revelar_capa(v["id"], v["denom"], v["n"] - 1)
                    p.removeBody(cid)
                    info["quitada"] = True
                    t_ultima_moneda = t

            # Fin del llenado -> los vasos con monedas arrancan; los vacíos se retiran
            if (not embalaje_hecho and spawn_hechas >= args.monedas
                    and all(i["quitada"] for i in monedas_vivas.values())
                    and t - t_ultima_moneda > 1.0):
                embalaje_hecho = True
                for v in vasos:
                    if v["n"] == 0:
                        p.removeBody(v["id"])
                vasos = [v for v in vasos if v["n"] > 0]
                for v in vasos:
                    v["estado"] = "VIAJANDO"
                total_vasos = len(vasos)

            # ---------------- 2) Banda: avance con cola y paradas ----------------
            activos = sorted([v for v in vasos if not v["tomado"]],
                             key=lambda v: -pos_x(v["id"]))
            limite_adelante = None
            # Mientras el tanque agarra y se aleja, el siguiente vaso espera un poco atrás
            retener = estado_tanque in ("ALINEAR", "BAJAR_BRAZO", "AGARRAR",
                                        "SUBIR_BRAZO") or (
                estado_tanque == "EN_RUTA" and idx_ruta == 0)
            for v in activos:
                x = pos_x(v["id"])
                meta_x = X_FIN_BANDA if v["tapado"] else X_TAPA
                if retener and v["tapado"]:
                    meta_x = min(meta_x, X_FIN_BANDA - 0.12)
                if limite_adelante is not None:
                    meta_x = min(meta_x, limite_adelante)
                if v["estado"] == "LLENANDO":
                    p.resetBaseVelocity(v["id"], [0, 0, 0], [0, 0, 0])
                elif x >= meta_x - 0.003:
                    p.resetBaseVelocity(v["id"], [0, 0, 0], [0, 0, 0])
                else:
                    p.resetBaseVelocity(v["id"], [V_BANDA, 0, 0], [0, 0, 0])
                v["en_estacion"] = (not v["tapado"] and v["estado"] != "LLENANDO"
                                    and x >= X_TAPA - 0.01)
                v["listo"] = v["tapado"] and x >= X_FIN_BANDA - 0.01
                limite_adelante = meta_x - SEP_VASOS

            # ---------------- 3) Brazo de tapas ----------------
            if brazo_estado == "REPOSO":
                cand = [v for v in vasos if v["en_estacion"] and not v["tapado"]]
                if cand:
                    vaso_brazo = cand[0]
                    brazo_mover(brazo, yaw=YAW_ALMACEN, bajar=SLIDE_BAJO, cerrar=False)
                    brazo_estado, t_brazo = "BAJAR_A_TAPA", t
            elif brazo_estado == "BAJAR_A_TAPA":
                if brazo_alcanzo(brazo, bajar=SLIDE_BAJO) or t - t_brazo > 4:
                    brazo_mover(brazo, cerrar=True)
                    brazo_estado, t_brazo = "AGARRAR_TAPA", t
            elif brazo_estado == "AGARRAR_TAPA":
                if brazo_alcanzo(brazo, cerrar=True) or t - t_brazo > 2:
                    tapa_en_brazo(brazo, True, vaso_brazo["denom"])
                    brazo_mover(brazo, bajar=0.0)
                    brazo_estado, t_brazo = "SUBIR_CON_TAPA", t
            elif brazo_estado == "SUBIR_CON_TAPA":
                if brazo_alcanzo(brazo, bajar=0.0) or t - t_brazo > 4:
                    brazo_mover(brazo, yaw=YAW_VASO)
                    brazo_estado, t_brazo = "GIRAR_A_VASO", t
            elif brazo_estado == "GIRAR_A_VASO":
                if brazo_alcanzo(brazo, yaw=YAW_VASO) or t - t_brazo > 5:
                    brazo_mover(brazo, bajar=SLIDE_BAJO)
                    brazo_estado, t_brazo = "BAJAR_A_VASO", t
            elif brazo_estado == "BAJAR_A_VASO":
                if brazo_alcanzo(brazo, bajar=SLIDE_BAJO) or t - t_brazo > 4:
                    brazo_mover(brazo, cerrar=False)
                    brazo_estado, t_brazo = "SOLTAR_TAPA", t
            elif brazo_estado == "SOLTAR_TAPA":
                if brazo_alcanzo(brazo, cerrar=False) or t - t_brazo > 2:
                    tapa_en_brazo(brazo, False)
                    poner_tapa_vaso(vaso_brazo["id"], vaso_brazo["denom"])
                    vaso_brazo["tapado"] = True
                    tapadas += 1
                    brazo_mover(brazo, bajar=0.0)
                    brazo_estado, t_brazo = "SUBIR_SIN_TAPA", t
            elif brazo_estado == "SUBIR_SIN_TAPA":
                if brazo_alcanzo(brazo, bajar=0.0) or t - t_brazo > 4:
                    brazo_mover(brazo, yaw=YAW_ALMACEN)
                    brazo_estado, t_brazo = "VOLVER", t
            elif brazo_estado == "VOLVER":
                if brazo_alcanzo(brazo, yaw=YAW_ALMACEN) or t - t_brazo > 5:
                    brazo_estado = "REPOSO"

            # ---------------- 4) Tanque (máquina de estados) ----------------
            muneca_paralela(tanque)                  # la pinza se mantiene derecha
            if estado_tanque == "ESPERANDO_VASO":
                mover(tanque, 0, 0)
                brazo_t(tanque, True)                 # servo 1: brazo arriba
                pinza(tanque, False)                  # servos 2 y 3: pinza abierta
                cand = [v for v in vasos if v["listo"] and not v["tomado"]]
                if cand:
                    vaso_tanque = cand[0]
                    estado_tanque = "ACERCARSE"
            elif estado_tanque == "ACERCARSE":
                if ir_a(tanque, PICK_POS, tol=0.025):
                    estado_tanque = "ALINEAR"
            elif estado_tanque == "ALINEAR":
                if orientar(tanque, PICK_YAW):
                    brazo_t(tanque, False)            # servo 1: baja el brazo
                    t_estado = t
                    estado_tanque = "BAJAR_BRAZO"
            elif estado_tanque == "BAJAR_BRAZO":
                mover(tanque, 0, 0)
                if brazo_t_listo(tanque, False) or t - t_estado > 4.0:
                    pinza(tanque, True)               # servos 2 y 3: cierran
                    t_estado = t
                    estado_tanque = "AGARRAR"
            elif estado_tanque == "AGARRAR":
                mover(tanque, 0, 0)
                if t - t_estado > 1.5:
                    constraint = sujetar(tanque, vaso_tanque["id"])
                    vaso_tanque["tomado"] = True
                    brazo_t(tanque, True)             # servo 1: sube con el vaso
                    t_estado = t
                    estado_tanque = "SUBIR_BRAZO"
            elif estado_tanque == "SUBIR_BRAZO":
                mover(tanque, 0, 0)
                if brazo_t_listo(tanque, True) or t - t_estado > 4.0:
                    ruta_actual = ruta_entrega(entregados, total_vasos)
                    idx_ruta = 0
                    trayectoria.clear()
                    estado_tanque = "EN_RUTA"
            elif estado_tanque == "EN_RUTA":
                if idx_ruta < len(ruta_actual):
                    es_ultimo = idx_ruta == len(ruta_actual) - 1   # punto de entrega: más preciso
                    if ir_a(tanque, ruta_actual[idx_ruta], tol=0.03 if es_ultimo else 0.07):
                        idx_ruta += 1
                elif orientar(tanque, _psi_fin, tol=0.05):
                    # el vaso cae 0.23 m delante del tanque: se apunta hacia el final de la pista
                    brazo_t(tanque, False)            # servo 1: baja el vaso
                    estado_tanque = "BAJAR_ENTREGA"
                    t_estado = t
            elif estado_tanque == "BAJAR_ENTREGA":
                mover(tanque, 0, 0)
                if brazo_t_listo(tanque, False) or t - t_estado > 4.0:
                    estado_tanque = "SOLTAR"
                    t_estado = t
            elif estado_tanque == "SOLTAR":
                mover(tanque, 0, 0)
                if constraint is not None:
                    p.removeConstraint(constraint)
                    constraint = None
                    pinza(tanque, False)              # servos 2 y 3: abren
                    t_estado = t
                elif pinza_abierta(tanque) or t - t_estado > 2.0:
                    brazo_t(tanque, True)             # servo 1: sube sin vaso
                    estado_tanque = "SUBIR_VACIO"
                    t_estado = t
            elif estado_tanque == "SUBIR_VACIO":
                mover(tanque, 0, 0)
                if brazo_t_listo(tanque, True) or t - t_estado > 4.0:
                    vaso_tanque["entregado"] = True
                    entregados += 1
                    if entregados >= total_vasos:
                        meta_ok = True
                        estado_tanque = "FIN"
                        t_fin = t
                    else:
                        ruta_vuelta = ruta_regreso(ruta_actual)
                        idx_ruta = 0
                        x_, y_, _ = pose2d(tanque)
                        # no dar media vuelta por puntos que ya quedan casi encima
                        while (idx_ruta < len(ruta_vuelta) - 1 and
                               math.hypot(ruta_vuelta[idx_ruta][0] - x_,
                                          ruta_vuelta[idx_ruta][1] - y_) < 0.25):
                            idx_ruta += 1
                        estado_tanque = "REGRESAR"
            elif estado_tanque == "REGRESAR":
                if ir_a(tanque, ruta_vuelta[idx_ruta], tol=0.08):
                    idx_ruta += 1
                    if idx_ruta >= len(ruta_vuelta):
                        estado_tanque = "ESPERANDO_VASO"
            else:  # FIN
                mover(tanque, 0, 0)

            if args.traza and estado_tanque != estado_previo:
                x_, y_, yaw_ = pose2d(tanque)
                print(f"[{t:7.2f}s] tanque -> {estado_tanque:14s} pos=({x_:.2f},{y_:.2f}) "
                      f"brazo={math.degrees(p.getJointState(tanque, J_BRAZO_T)[0]):.0f}° "
                      f"colisiones={colisiones}")
                if estado_tanque == "SUBIR_VACIO" and vaso_tanque is not None:
                    vx, vy, vz = p.getBasePositionAndOrientation(vaso_tanque["id"])[0]
                    dentro = (abs(vx - META[0]) < META_H[0] + 0.05 and abs(vy - META[1]) < META_H[1] + 0.05)
                    print(f"           vaso entregado en ({vx:.2f},{vy:.2f}) z={vz:.3f} "
                          f"{'DENTRO' if dentro else 'FUERA'} de la zona de meta")
                estado_previo = estado_tanque

            # Colisiones del tanque con los muros (flancos de subida)
            for i, m in enumerate(muros):
                cps = p.getContactPoints(tanque, m)
                hay = len(cps) > 0
                if hay and not en_contacto[i]:
                    colisiones += 1
                    if args.traza:
                        x_, y_, yaw_ = pose2d(tanque)
                        print(f"[{t:7.2f}s] CHOQUE con muro {i + 1}: pos=({x_:.2f},{y_:.2f}) "
                              f"yaw={yaw_:.2f} links={sorted({c[3] for c in cps})}")
                en_contacto[i] = hay

            p.stepSimulation()
            t += DT

            # ---------------- 5) Textos en pantalla y JSON ----------------
            if t - t_json >= PERIODO_JSON:
                t_json = t
                x, y, yaw = pose2d(tanque)
                if args.traza and int(t / PERIODO_JSON) % 4 == 0:
                    print(f"   t={t:7.2f}s  {estado_tanque:13s} pos=({x:.2f},{y:.2f}) yaw={yaw:.2f}"
                          + (f" -> objetivo={ruta_vuelta[idx_ruta]}" if estado_tanque == "REGRESAR"
                             else "")
                          + f" rp={[round(a, 2) for a in p.getEulerFromQuaternion(p.getBasePositionAndOrientation(tanque)[1])[:2]]}"
                          + f" zb={p.getBasePositionAndOrientation(tanque)[0][2]:.3f}"
                          + f" wv={[round(p.getJointState(tanque, j)[1], 1) for j in range(6)]}"
                          + "  contactos=" + str(sorted({(c[2], c[3], c[4])
                                                        for c in p.getContactPoints(tanque)
                                                        if c[2] != 0})))
                activo = estado_tanque in ("EN_RUTA", "BAJAR_ENTREGA", "SOLTAR",
                                           "SUBIR_VACIO", "FIN")
                if estado_tanque in ("EN_RUTA", "BAJAR_ENTREGA", "SOLTAR",
                                     "SUBIR_VACIO"):
                    s_t = pista_s(x, y)
                    superados = sum(1 for o in OBST if s_t > o["s"] + o["largo"] / 2 + 0.10)
                elif estado_tanque in ("REGRESAR", "FIN"):
                    superados = len(OBST)
                else:
                    superados = 0
                total_cont = sum(cuentas.values())
                valor_total = sum(d * n for d, n in cuentas.items())
                aciertos = sum(min(cuentas[d], reales[d]) for d in DENOMS)

                clave = (tuple(cuentas.values()), entregados, tapadas, estado_tanque)
                if clave != ultimo_texto:
                    ultimo_texto = clave
                    for xc, d in zip(X_CANALES, DENOMS):
                        texto(f"${d}: {cuentas[d]}", [xc - 0.05, -0.16, 0.22],
                              size=1.0,
                              color=tuple(c * 0.55 for c in COLOR_DENOM[d][:3]),
                              reemplazar=ids_canal[d])
                    texto(f"Total: {fmt_pesos(valor_total)} | Monedas: {total_cont}"
                          f" | Tapas: {tapadas} | Entregados: {entregados}/{total_vasos}",
                          [-0.45, 0, 0.93], size=1.2, color=(0.1, 0.1, 0.1),
                          reemplazar=id_hud)
                    texto("Tanque: " + NOMBRES_ESTADO[estado_tanque],
                          [1.6 + DX, -0.45, 0.25], size=1.1, color=(0.1, 0.3, 0.7),
                          reemplazar=id_tanque)

                if activo:
                    trayectoria.append([round(x, 3), round(y, 3)])
                serie.append([round(t, 1), valor_total, total_cont])
                del trayectoria[:-MAX_HIST]
                del serie[:-MAX_HIST]

                escribir_estado({
                    "t": round(t, 2),
                    "origen": "pybullet",
                    "cuentas": {str(d): cuentas[d] for d in DENOMS},
                    "valor_total": valor_total,
                    "monedas_contadas": total_cont,
                    "monedas_lanzadas": spawn_hechas,
                    "monedas_objetivo": args.monedas,
                    "precision": round(aciertos / total_cont, 3) if total_cont else None,
                    "vasos_embalados": tapadas,
                    "vasos_entregados": entregados,
                    "total_vasos": total_vasos,
                    "vasos": [{"denom": v["denom"], "n": v["n"], "valor": v["valor"],
                               "tapado": v["tapado"], "entregado": v["entregado"]}
                              for v in vasos],
                    "valores_vasos": [v["valor"] for v in vasos],
                    "brazo_tapas": {"estado": brazo_estado, "tapas_puestas": tapadas},
                    "tanque": {
                        "x": round(x, 3), "y": round(y, 3), "yaw": round(yaw, 3),
                        "estado": estado_tanque,
                        "servos": {
                            "brazo_deg": round(math.degrees(p.getJointState(tanque, J_BRAZO_T)[0]), 1),
                            "pinza_izq_deg": round(math.degrees(
                                p.getJointState(tanque, J_DEDO_T_I)[0]), 1),
                            "pinza_der_deg": round(math.degrees(
                                p.getJointState(tanque, J_DEDO_T_D)[0]), 1),
                        },
                        "obstaculos_superados": superados,
                        "obstaculos_total": len(OBST),
                        "colisiones": colisiones,
                    },
                    "meta_alcanzada": meta_ok,
                    "trayectoria": trayectoria,
                    "serie": serie,
                    "escenario": ESCENARIO,
                })

            if not args.sin_gui:
                time.sleep(DT / max(args.velocidad, 0.1))
            if t_fin is not None and t - t_fin > 4.0:
                break
    except (KeyboardInterrupt, p.error):
        pass
    finally:
        print("Fin de la simulación. Cuentas:", cuentas, "| tapas:", tapadas,
              "| entregados:", entregados, "| meta:", meta_ok,
              "| colisiones:", colisiones)
        if p.isConnected():
            p.disconnect()


if __name__ == "__main__":
    main()
