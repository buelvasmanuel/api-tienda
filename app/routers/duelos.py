"""Duelo de cartas (Modo 4). Contrato: docs/CONTRATO_DUELO.md.

Requiere sesión. El servidor reparte, valida y decide; las respuestas van
siempre desde el punto de vista de quien pregunta ("yo" / "rival").

v2 (jugador contra jugador): los plazos se aplican al leer o escribir el
duelo (aplicar_plazos) y, tras cada cambio, se avisa a los dos jugadores por
el WebSocket (/duelos/{id}/ws), que solo avisa: todo lo que cambia el duelo
sigue entrando por REST.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import database, logs
from app import duelos as reglas
from app import progreso, schemas
from app.config import settings
from app.database import get_db
from app.dependencias import AccesoDenegado, autenticar, usuario_actual
from app.errores import error
from app.models import Duelo, Usuario
from app.routers.juego import bloquear_usuario, insignias_out, resumen
from app.tiempo_real import central

log = logs.obtener("app.duelos")

router = APIRouter(prefix="/duelos", tags=["Duelo"])
con_sesion = [Depends(usuario_actual)]

ESPERA_AUTENTICACION = 10  # segundos para enviar {"tipo": "auth"} tras abrir el WebSocket


# ---------- Respuesta desde el punto de vista de quien pregunta ----------

def _perspectiva(lado_ronda: str, soy: str) -> str:
    return "empate" if lado_ronda == "empate" else "yo" if lado_ronda == soy else "rival"


def _rival_id(duelo: Duelo, soy: str) -> int | None:
    return duelo.jugador_b_id if soy == "a" else duelo.jugador_a_id


def _rival(db: Session, duelo: Duelo, soy: str) -> schemas.RivalOut:
    if duelo.modo == "maquina":
        return schemas.RivalOut(tipo="maquina", alias=reglas.ALIAS_MAQUINA)
    rival_id = _rival_id(duelo, soy)
    return schemas.RivalOut(tipo="jugador", alias=db.get(Usuario, rival_id).alias if rival_id else None)


def _mazos(duelo: Duelo, soy: str) -> tuple[list, list]:
    return (duelo.mazo_a, duelo.mazo_b) if soy == "a" else (duelo.mazo_b, duelo.mazo_a)


def duelo_out(db: Session, duelo: Duelo, usuario_id: int, insignias=(), progreso_=None) -> schemas.DueloOut:
    soy = reglas.lado_de(duelo, usuario_id)
    mio, suyo = _mazos(duelo, soy)
    puntos_yo, puntos_rival = (duelo.puntos_a, duelo.puntos_b) if soy == "a" else (duelo.puntos_b, duelo.puntos_a)
    rondas = []
    for r in duelo.rondas:
        mia, rival = mio[r["numero"] - 1], suyo[r["numero"] - 1]
        rondas.append(schemas.RondaOut(
            numero=r["numero"], elegido_por=_perspectiva(r["elegido_por"], soy), atributo=r["atributo"],
            motivo=r.get("motivo", "jugada"), mi_carta=mia, carta_rival=rival,
            ganador=_perspectiva(r["ganador"], soy), explicacion=reglas.explicacion(r, mia, rival, soy),
        ))
    turno = reglas.turno(duelo)
    rival_id = _rival_id(duelo, soy)
    return schemas.DueloOut(
        id=duelo.id, modo=duelo.modo, estado=duelo.estado, rival=_rival(db, duelo, soy),
        rondas_total=reglas.RONDAS, ronda=duelo.ronda,
        turno=None if turno is None else ("yo" if turno == soy else "rival"),
        marcador=schemas.MarcadorOut(yo=puntos_yo, rival=puntos_rival),
        # Solo la carta propia de la ronda en curso: nunca la del rival ni las siguientes.
        mi_carta=mio[duelo.ronda - 1] if duelo.ronda else None,
        rondas=rondas,
        resultado=reglas.resultado_de(duelo, soy),
        xp_ganada=duelo.xp_a if soy == "a" else duelo.xp_b,
        duelos_con_xp_hoy=schemas.DuelosConXpOut(
            jugados=min(reglas.con_xp_hoy(db, usuario_id, duelo.modo), reglas.DUELOS_CON_XP_AL_DIA),
            maximo=reglas.DUELOS_CON_XP_AL_DIA),
        atributos=[schemas.AtributoOut(**vars(a)) for a in reglas.ATRIBUTOS.values()],
        expira_turno_en=duelo.expira_turno_en,
        rival_en_linea=None if duelo.modo == "maquina" or rival_id is None else central.en_linea(duelo.id, rival_id),
        creado_en=duelo.creado_en, actualizado_en=duelo.actualizado_en,
        insignias_nuevas=insignias_out(insignias), progreso=progreso_,
    )


def _avisar(db: Session, duelo: Duelo, tipo: str) -> None:
    """Aviso por WebSocket a los dos jugadores, cada uno con su punto de vista.
    Siempre después del commit: lo que se avisa ya está guardado."""
    for usuario_id in (duelo.jugador_a_id, duelo.jugador_b_id):
        if usuario_id is not None:
            central.avisar(duelo.id, usuario_id, {"tipo": tipo, "duelo": duelo_out(db, duelo, usuario_id).model_dump(mode="json")})


def _tipo_de_aviso(duelo: Duelo) -> str:
    return "terminado" if duelo.estado in ("terminado", "abandonado") else "ronda_resuelta"


# ---------- Acceso ----------

def _mio(db: Session, duelo_id: str, usuario_id: int, bloquear: bool = False) -> Duelo:
    """El duelo, si existe y el usuario juega en él (404 en los dos casos)."""
    consulta = select(Duelo).where(Duelo.id == duelo_id)
    duelo = db.scalar(consulta.with_for_update() if bloquear else consulta)
    if duelo is None or reglas.lado_de(duelo, usuario_id) is None:
        raise error(status.HTTP_404_NOT_FOUND, "duelo_no_encontrado", "No existe ese duelo")
    return duelo


def _http(e: reglas.JugadaInvalida):
    return error(e.estado, e.codigo, e.mensaje, **e.extra)


def _con_plazos(db: Session, duelo_id: str, usuario_id: int) -> Duelo:
    """Lee el duelo bloqueándolo y aplica los vencimientos pendientes (v2); si
    cambió algo, lo guarda y avisa a los dos."""
    duelo = _mio(db, duelo_id, usuario_id, bloquear=True)
    if reglas.aplicar_plazos(db, duelo):
        db.commit()
        _avisar(db, duelo, _tipo_de_aviso(duelo))
    return duelo


# ---------- REST ----------

@router.post("", response_model=schemas.DueloOut, status_code=status.HTTP_201_CREATED, dependencies=con_sesion)
def crear(datos: schemas.NuevoDueloEntrada, respuesta: Response, usuario: Usuario = Depends(usuario_actual),
          db: Session = Depends(get_db)):
    bloquear_usuario(db, usuario)  # dos "nuevo duelo" a la vez del mismo jugador van en fila
    try:
        if datos.modo == "maquina":
            duelo, nuevo = reglas.crear_contra_maquina(db, usuario.id), True
        else:
            # El candado pone en fila a todos los que buscan rival: dos que buscan
            # a la vez se emparejan entre sí en vez de quedarse los dos esperando.
            with reglas.candado(db, reglas.CANDADO_EMPAREJAMIENTO):
                duelo, nuevo = reglas.buscar_rival(db, usuario.id)
                db.commit()
    except reglas.JugadaInvalida as e:
        db.rollback()
        raise _http(e)
    db.commit()
    if not nuevo:
        respuesta.status_code = status.HTTP_200_OK  # ya estaba esperando: el mismo duelo
    elif duelo.modo == "jugador" and duelo.estado == "en_juego":
        _avisar(db, duelo, "emparejado")
    return duelo_out(db, duelo, usuario.id)


@router.get("", response_model=schemas.DuelosOut, dependencies=con_sesion)
def listar(estado: Literal["esperando", "en_juego", "terminado"] | None = None,
           usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    # Los vencimientos pendientes se aplican antes de listar.
    for pendiente in db.scalars(select(Duelo.id).where(
            or_(Duelo.jugador_a_id == usuario.id, Duelo.jugador_b_id == usuario.id),
            Duelo.modo == "jugador", Duelo.estado.in_(("esperando", "en_juego")))).all():
        _con_plazos(db, pendiente, usuario.id)
    consulta = (select(Duelo).where(or_(Duelo.jugador_a_id == usuario.id, Duelo.jugador_b_id == usuario.id))
                .order_by(Duelo.creado_en.desc(), Duelo.id).limit(20))
    if estado:
        consulta = consulta.where(Duelo.estado == estado)
    resumenes = []
    for duelo in db.scalars(consulta):
        d = duelo_out(db, duelo, usuario.id)
        resumenes.append(schemas.DueloResumenOut(id=d.id, modo=d.modo, estado=d.estado, rival=d.rival,
                                                 marcador=d.marcador, resultado=d.resultado,
                                                 xp_ganada=d.xp_ganada, creado_en=d.creado_en))
    xp_hoy = schemas.XpHoyOut(**{
        modo: schemas.DuelosConXpOut(jugados=min(reglas.con_xp_hoy(db, usuario.id, modo), reglas.DUELOS_CON_XP_AL_DIA),
                                     maximo=reglas.DUELOS_CON_XP_AL_DIA)
        for modo in ("maquina", "jugador")})
    db.commit()
    return schemas.DuelosOut(duelos=resumenes, xp_hoy=xp_hoy)


@router.get("/{duelo_id}", response_model=schemas.DueloOut, dependencies=con_sesion)
def obtener(duelo_id: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    duelo = _con_plazos(db, duelo_id, usuario.id)
    respuesta = duelo_out(db, duelo, usuario.id)
    db.commit()
    return respuesta


@router.post("/{duelo_id}/jugadas", response_model=schemas.DueloOut, dependencies=con_sesion)
def jugar(duelo_id: str, datos: schemas.JugadaEntrada, usuario: Usuario = Depends(usuario_actual),
          db: Session = Depends(get_db)):
    bloquear_usuario(db, usuario)  # la XP del final no puede darse dos veces
    duelo = _con_plazos(db, duelo_id, usuario.id)
    lado = reglas.lado_de(duelo, usuario.id)
    xp_antes = progreso.xp_total(db, usuario.id)
    try:
        jugada_nueva = reglas.jugar(db, duelo, lado, datos.ronda, datos.atributo)
    except reglas.JugadaInvalida as e:
        db.rollback()
        raise _http(e)
    nuevas, resumen_progreso, rival_id = [], None, _rival_id(duelo, lado)
    if jugada_nueva and duelo.estado == "terminado":
        db.flush()
        nuevas = progreso.otorgar(db, usuario.id)
        if rival_id is not None:
            # El rival ve su XP en el aviso "terminado"; sus insignias, en /juego/progreso.
            progreso.otorgar(db, rival_id)
        resumen_progreso = resumen(xp_antes, progreso.xp_total(db, usuario.id))
    db.commit()
    if jugada_nueva:
        _avisar(db, duelo, _tipo_de_aviso(duelo))
    return duelo_out(db, duelo, usuario.id, nuevas, resumen_progreso)


@router.post("/{duelo_id}/abandonar", response_model=schemas.DueloOut, dependencies=con_sesion)
def abandonar(duelo_id: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    duelo = _con_plazos(db, duelo_id, usuario.id)
    lado = reglas.lado_de(duelo, usuario.id)
    if duelo.estado == "abandonado" and reglas.resultado_de(duelo, lado) in ("derrota", None):
        db.commit()
        return duelo_out(db, duelo, usuario.id)  # repetirlo (o una búsqueda ya caducada) devuelve el mismo estado
    if duelo.estado not in ("en_juego", "esperando"):
        db.rollback()
        raise error(status.HTTP_409_CONFLICT, "duelo_terminado", "El duelo ya terminó")
    reglas.abandonar(db, duelo, lado)
    rival_id = _rival_id(duelo, lado)
    if rival_id is not None:
        db.flush()
        progreso.otorgar(db, rival_id)
    db.commit()
    _avisar(db, duelo, "terminado")
    return duelo_out(db, duelo, usuario.id)


# ---------- WebSocket de avisos (CONTRATO_DUELO §8) ----------

class _Cierre(Exception):
    def __init__(self, codigo: int, motivo: str):
        super().__init__(motivo)
        self.codigo, self.motivo = codigo, motivo


def _origen_permitido(origen: str | None) -> bool:
    """CORS no protege los WebSockets: el navegador conecta desde cualquier web.
    Las apps nativas no envían Origin (o no es una web): se aceptan."""
    if origen is None or "*" in settings.cors_origins:
        return True
    return origen.rstrip("/") in settings.cors_origins


def _entrar(token: str, duelo_id: str) -> tuple[int, dict]:
    """Autentica el token y comprueba que el usuario juega el duelo. Devuelve
    (usuario_id, estado inicial). Corre en un hilo, con una sesión corta."""
    with database.sesion_breve() as db:
        try:
            sesion = autenticar(db, token)
        except AccesoDenegado as e:
            raise _Cierre(4401 if e.estado == 401 else 4403, e.codigo)
        usuario_id = sesion.usuario.id
        try:
            duelo = _con_plazos(db, duelo_id, usuario_id)
        except Exception as e:  # noqa: BLE001  (HTTPException 404)
            raise _Cierre(4404, "duelo_no_encontrado") from e
        estado = duelo_out(db, duelo, usuario_id).model_dump(mode="json")
        db.commit()
        return usuario_id, estado


def _avisar_presencia(duelo_id: str, usuario_id: int, tipo: str) -> None:
    """Avisa al rival de que este jugador abrió o cerró su WebSocket."""
    with database.sesion_breve() as db:
        duelo = db.get(Duelo, duelo_id)
        if duelo is None or duelo.modo != "jugador":
            return
        rival_id = _rival_id(duelo, reglas.lado_de(duelo, usuario_id))
        if rival_id is not None:
            central.avisar(duelo_id, rival_id, {"tipo": tipo, "duelo": duelo_out(db, duelo, rival_id).model_dump(mode="json")})


async def _rechazar(websocket: WebSocket, duelo_id: str, codigo: int, motivo: str) -> None:
    """Cierra con código, avisando antes con un mensaje normal: detrás del
    proxy de Render/Cloudflare los mensajes llegan, pero el marco de cierre
    con su código no siempre (comprobado en producción)."""
    log.info("ws_cierre", duelo=duelo_id[:8], codigo=codigo, motivo=motivo)
    try:
        await websocket.send_json({"tipo": "error", "codigo": codigo, "motivo": motivo})
    finally:
        await websocket.close(code=codigo, reason=motivo)


@router.websocket("/{duelo_id}/ws")
async def canal(websocket: WebSocket, duelo_id: str):
    # Se acepta antes de rechazar: si se cerrara durante el handshake, el
    # navegador no vería el código 4403, solo un fallo de conexión.
    await websocket.accept()
    log.info("ws_aceptado", duelo=duelo_id[:8], origin=websocket.headers.get("origin"))
    if not _origen_permitido(websocket.headers.get("origin")):
        await _rechazar(websocket, duelo_id, 4403, "origen_no_permitido")
        return
    try:
        try:
            primero = await asyncio.wait_for(websocket.receive_json(), timeout=ESPERA_AUTENTICACION)
        except TimeoutError:
            raise _Cierre(4408, "sin_autenticacion")
        if not isinstance(primero, dict) or primero.get("tipo") != "auth" or not isinstance(primero.get("token"), str):
            raise _Cierre(4401, "no_autenticado")
        usuario_id, estado = await run_in_threadpool(_entrar, primero["token"], duelo_id)
    except _Cierre as c:
        await _rechazar(websocket, duelo_id, c.codigo, c.motivo)
        return
    except (WebSocketDisconnect, ValueError, KeyError):
        return

    central.conectar(duelo_id, usuario_id, websocket)
    log.info("ws_autenticado", duelo=duelo_id[:8], usuario_id=usuario_id)
    try:
        await websocket.send_json({"tipo": "listo", "duelo": estado})
        await run_in_threadpool(_avisar_presencia, duelo_id, usuario_id, "rival_conectado")
        while True:
            mensaje = await websocket.receive_json()
            if isinstance(mensaje, dict) and mensaje.get("tipo") == "ping":
                await websocket.send_json({"tipo": "pong"})
            # Cualquier otro mensaje se ignora: las jugadas van por REST.
    except (WebSocketDisconnect, ValueError, KeyError):  # cierre, o mensaje que no es JSON
        pass
    finally:
        central.desconectar(duelo_id, usuario_id, websocket)
        await run_in_threadpool(_avisar_presencia, duelo_id, usuario_id, "rival_desconectado")
