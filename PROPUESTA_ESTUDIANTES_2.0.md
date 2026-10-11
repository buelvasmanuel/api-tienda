# Estudiantes 2.0 — De app informativa a juego de datos

> Documento de trabajo para el proyecto `api-tienda` (backend) + `app_estudiantes` (frontend).
> Objetivo de la semana (hasta el sábado 17/10/2026): **experimentar e implementar con calidad**, no entregar rápido.
> Este archivo sirve como contexto para Claude Code: la sección 1 describe lo que existe, la 2-5 lo que proponemos, la 6 el plan.

---

## 0. Cómo usar este documento

1. Guárdalo en la raíz de los dos repos (o en una carpeta `docs/`).
2. La sección 1 está escrita a partir de lo construido en la sesión del 10/10. **Claude Code debe verificarla contra el código real** y generar `docs/ARQUITECTURA.md` con los detalles exactos (rutas, clases, tablas).
3. Las decisiones marcadas con 🟡 están abiertas: se deciden antes de implementar esa parte.

---

## 1. Lo que existe hoy

### 1.1 Visión general

```
┌──────────────────────────┐   HTTPS/JSON   ┌───────────────────────────┐   SSL   ┌──────────────────┐
│  app_estudiantes         │ ─────────────► │  api-tienda (FastAPI)     │ ──────► │ MySQL 8.4 (Aiven)│
│  Flutter · Material 3    │ ◄───────────── │  Render free · Oregon     │         │ DigitalOcean sfo │
│  Web (GitHub Pages) +APK │                │  modelo sklearn (.joblib) │         │ 300 estudiantes  │
└──────────────────────────┘                └───────────────────────────┘         └──────────────────┘
```

### 1.2 Backend — `api-tienda`

| Capa | Tecnología | Notas |
|---|---|---|
| Framework | FastAPI + Uvicorn | OpenAPI/Swagger en `/docs` |
| ORM | SQLAlchemy 2.x (`Mapped`, `mapped_column`, `select()`) | Sesión por petición con `Depends(get_db)` |
| Validación | Pydantic v2 + pydantic-settings | Rangos con `Annotated`, `str_strip_whitespace` |
| Base de datos | MariaDB (local) / MySQL 8.4 (Aiven) | Collation `utf8mb4_unicode_ci` forzada por evento `connect` |
| Migraciones | Alembic (4 migraciones, `naming_convention`) | CHECK constraints en `estudiantes` |
| ML | scikit-learn + pandas + joblib | Regresión logística balanceada, elegida por F2 con CV estratificada |
| Tests | pytest + httpx2, base `tienda_test` | ~133 tests, rollback por savepoint, `dependency_overrides` |
| Scripts | `seed_estudiantes` (Faker es_CO, semilla fija), `graficas` (matplotlib), `entrenar_modelo` | |
| Despliegue | Render (Blueprint `render.yaml`), Aiven MySQL | Modelo se entrena en el build; sin auto-deploy |

**Entidades:** `Producto`, `Categoria` (legado de la actividad inicial), `Estudiante` (nombre, apellido, edad, carrera, semestre, promedio, ciudad, horas_estudio, asistencia).

**Endpoints principales:**
- CRUD: `/productos`, `/categorias`, `/estudiantes`
- Estadísticas (`[{etiqueta, valor}]`): `/estudiantes/estadisticas/{por-carrera, por-edad, por-semestre, por-ciudad, promedio-por-carrera, riesgo-por-carrera}`
- Predicción: `POST /prediccion/riesgo`, `GET /prediccion/metricas`

**Lo que NO tiene:** usuarios, autenticación, autorización, progreso persistente, tiempo real, caché, rate limiting.

### 1.3 Frontend — `app_estudiantes`

| Capa | Tecnología | Notas |
|---|---|---|
| UI | Flutter 3.47 · Material 3 (tema claro/oscuro) | NavigationBar + IndexedStack, 3 pestañas |
| HTTP | `http` + `ApiService` propio | `ApiException`, timeouts 8 s / 60 s (arranque en frío de Render) |
| Gráficas | `fl_chart` | Barras con etiquetas adaptables (horizontal / 45° / scroll) |
| Estado | `setState` + `ValueNotifier` | Sin gestor de estado dedicado |
| Navegación | `Navigator` imperativo | Sin rutas declarativas ni deep links |
| Tests | flutter_test + `MockClient` + integration_test | ~36 tests |
| Plataformas | Android (APK release), Web (GitHub Pages vía Actions), Linux, Windows | |

**Pantallas:** Estudiantes (lista + buscador + filtro + detalle), Gráficas (6 estadísticas), Predicción (formulario + métricas del modelo).

### 1.4 Límites de la infraestructura gratuita (condicionan el diseño)

| Servicio | Límite | Consecuencia |
|---|---|---|
| Render free | Se duerme tras ~15 min, arranque ~50 s, sin disco persistente | Nada se guarda en archivos en runtime; todo estado va a la BD |
| Aiven free | 1 CPU, 1 GB RAM, 1 GB disco, se apaga si no se usa | Suficiente; ojo con tablas de eventos que crezcan sin control |
| GitHub Pages | Solo estático | La web es solo el cliente Flutter |

---

## 2. El concepto: "Campus Detective" 🔍

### 2.1 La idea en una frase

> Eres un **detective de datos** que llega a un campus misterioso. Cada edificio es una carrera, cada ventana un estudiante. Para resolver los casos tienes que **mirar los datos**, y para salvar a los estudiantes en riesgo tienes que **entender el modelo**.

Por qué funciona:
- **No es un dashboard disfrazado:** cada dato que el usuario ve, lo ve porque lo necesita para avanzar.
- **Lo entiende un niño:** "este edificio es más alto porque tiene más estudiantes", "las ventanas rojas están en peligro".
- **Es distinto al planetario 3D** de tus compañeros: es un mundo explorable y legible, no un espectáculo visual.

### 2.2 Los cuatro modos de juego

#### 🗺️ Modo 1 — El Mapa del Campus (exploración)
- Vista **isométrica 2.5D** (como los juegos de construir ciudades), hecha con Flame.
- **Edificio = carrera.** Altura = número de estudiantes. Color de las ventanas = proporción en riesgo (verde → ámbar → rojo).
- **Barrios = ciudades de origen** o **pisos = semestres** (🟡 decidir cuál dimensión se ve mejor).
- Tocar un edificio → se abre su **expediente**: estadísticas de esa carrera, con las gráficas de `fl_chart` existentes integradas como "fichas" del caso.
- Es la "gráfica avanzada", pero cada elemento visual **codifica un dato real**.

#### 📁 Modo 2 — Los Casos (misiones)
- Preguntas que se resuelven explorando el mapa o las gráficas:
  - *"¿En qué carrera hay más estudiantes en riesgo? Ve y márcala."*
  - *"¿La edad influye en el riesgo? Compara dos edificios y decide."*
  - *"Encuentra la ciudad con más estudiantes."*
- Respuesta correcta → XP + pista narrativa. Incorrecta → el búho guía explica por qué (sin castigo fuerte).
- Las respuestas **se calculan en el backend** con los datos reales; si cambian los datos, cambian las respuestas.
- Casos por niveles: Novato (leer una gráfica) → Investigador (comparar) → Experto (correlación vs causalidad, el "patrón espurio" de la edad).

#### 🧪 Modo 3 — El Laboratorio de Rescate (el modelo predictivo como puzzle)
Es el corazón del juego y lo que lo hace único.
- Aparece un estudiante con **probabilidad de riesgo alta** (ej. 78 %).
- Tienes un **presupuesto de intervenciones** (ej. 5 fichas): +2 h de estudio, tutoría (+10 % asistencia), etc.
- Cada movimiento llama al modelo y el **medidor de riesgo** se mueve en vivo.
- Objetivo: bajarlo de 50 % gastando lo mínimo. Más eficiente = más estrellas (⭐⭐⭐).
- Al terminar, una pantalla de **explicabilidad** (SHAP): *"La asistencia pesó 3 veces más que las horas de estudio"*.
- Así el usuario **aprende cómo piensa el modelo jugando**, que es la meta de la minería de datos.

#### 🃏 Modo 4 — Duelo de Cartas (minijuego, opcional)
- Cada estudiante es una **carta** con sus stats (asistencia, horas, promedio…), estilo "Top Trumps".
- Eliges un atributo; gana la carta con el valor más alto. Enseña a **comparar variables** y a intuir distribuciones.
- 🟡 Puede ser contra la máquina (fácil) o contra otro jugador en tiempo real (WebSockets, sección 4.4).

### 2.3 Progresión
- **XP y niveles** del detective (Novato → Investigador → Inspector → Jefe de datos).
- **Insignias:** "Primer rescate", "Rescate perfecto ⭐⭐⭐", "Ojo de halcón" (5 casos sin fallo).
- **Tabla de clasificación** semanal (opcional, ver Redis).
- El progreso se guarda en el backend → **requiere cuentas de usuario** → ver sección 4 (seguridad).

### 2.4 Reglas de diseño "anti-caos"
Lo que separa un juego bacano de uno ilegible:

1. **Una animación protagonista por pantalla.** El resto, transiciones discretas.
2. **Movimiento con propósito:** cada animación comunica algo (algo cambió, algo se ganó, algo se perdió). Nada se mueve "porque sí".
3. **Duraciones cortas:** 150-400 ms en la interfaz; solo las celebraciones duran más (≤ 1,5 s y se pueden saltar).
4. **Texto siempre quieto:** nunca se anima un texto que hay que leer.
5. **Paleta limitada:** colores semánticos fijos (verde = bien, ámbar = alerta, rojo = riesgo) + 1 color de marca.
6. **Respeta "reducir movimiento"** del sistema operativo (`MediaQuery.disableAnimations`): el juego debe poder jugarse sin animaciones.
7. **Sonido opcional** y apagado por defecto en la web.
8. **Datos legibles siempre:** detrás de cada elemento visual hay un número consultable (tocar/mantener).

---

## 3. Tecnologías propuestas — Frontend (Flutter)

### 3.1 Flame — motor de juegos 2D para Flutter ⭐ clave
- **Qué es:** un motor de juegos construido sobre Flutter (bucle de juego, sprites, cámara, colisiones, entrada táctil). Funciona en móvil, web y escritorio.
- **Para qué aquí:** el **mapa isométrico del campus**, el laboratorio y el duelo de cartas. Los menús, HUD y formularios siguen siendo widgets normales de Flutter encima del juego (patrón recomendado: Flame para entidades del mundo, widgets para la interfaz).
- **Por qué 2.5D y no 3D real:** en Flutter el ecosistema 2D es maduro y el 3D está en etapa temprana (`flame_3d` existe, pero es experimental y exige versiones muy recientes de Flutter). El isométrico da sensación de profundidad, es legible y estable.
- **Alternativa:** dibujar con `CustomPainter` puro (más control, más trabajo).

### 3.2 Rive — animaciones interactivas con máquina de estados ⭐ clave
- **Qué es:** herramienta de diseño + runtime para animaciones vectoriales **interactivas**. Su "state machine" tiene entradas (booleanos, números, triggers) que Flutter controla en tiempo real.
- **Para qué aquí:** el **búho guía** (mascota) que reacciona: celebra, se preocupa, piensa, señala. Y el **medidor de riesgo** del laboratorio (una aguja o termómetro animado controlado por el número de probabilidad).
- **Ventaja frente a Lottie:** Lottie reproduce animaciones; Rive **reacciona** a datos. Un número de la API puede mover directamente la animación.
- **Nota:** hay mascotas gratuitas en la comunidad de Rive; diseñar una propia es opcional.

### 3.3 flutter_animate — micro-animaciones declarativas
- **Qué es:** librería para encadenar efectos (fade, slide, scale, shimmer) con una línea: `.animate().fadeIn().slideY()`.
- **Para qué aquí:** entradas de tarjetas, XP que sube, insignias desbloqueadas. Respeta la regla de "duraciones cortas" de forma consistente.

### 3.4 Riverpod — gestión de estado
- **Qué es:** sistema de estado reactivo y testeable (sucesor espiritual de Provider). Separa la lógica de la UI y cachea peticiones.
- **Por qué ahora:** con usuario autenticado, XP, progreso, caché de estadísticas y el juego, `setState` se vuelve inmanejable. Riverpod permite que el mapa, el HUD y el perfil compartan estado sin pasarlo a mano.
- **Alternativa:** Bloc (más ceremonia, muy popular en empresas). 🟡 decidir.

### 3.5 go_router — navegación declarativa
- **Qué es:** el router oficial recomendado por el equipo de Flutter. Rutas como URLs (`/campus`, `/caso/12`, `/laboratorio`).
- **Para qué aquí:** deep links en la web (compartir un caso), redirección automática a login si no hay sesión, y botón "atrás" correcto en el navegador.

### 3.6 freezed + json_serializable — modelos inmutables
- **Qué es:** generación de código para clases inmutables con `copyWith`, igualdad y `fromJson/toJson` automáticos.
- **Para qué aquí:** reemplaza el parseo manual a medida que crecen los modelos (usuario, progreso, casos, cartas). Se mantiene `leerNum` para el caso del promedio como texto.

### 3.7 flutter_secure_storage — almacén seguro
- **Qué es:** guarda secretos en el Keychain (iOS), Keystore (Android) o equivalente.
- **Para qué aquí:** el **refresh token** de la sesión. Nunca en `SharedPreferences` (texto plano).
- **Ojo web:** en navegador no existe un almacén realmente seguro; ver estrategia de cookies en 4.2.

### 3.8 Audio — flame_audio
- Efectos cortos (acierto, rescate, insignia) y música ambiental suave. Opcional, con interruptor.

---

## 4. Tecnologías propuestas — Backend

### 4.1 Aclaración: "JWT de Spring Boot"
**JWT no es de Spring Boot.** JWT (JSON Web Token) es un **estándar abierto** de tokens que se usa en cualquier lenguaje. Spring Boot (Java) lo usa muchísimo a través de *Spring Security*, por eso se asocia. Nuestro backend es Python/FastAPI y puede usar JWT o algo mejor.

**El problema conocido de JWT:** el propio token dice qué algoritmo usar (campo `alg`). Implementaciones descuidadas han aceptado `alg: none` o confundido algoritmos, permitiendo falsificar tokens. No es que JWT sea inseguro, es que **es fácil configurarlo mal**.

### 4.2 Propuesta de seguridad (recomendada) 🔐

| Pieza | Qué es | Por qué |
|---|---|---|
| **PASETO v4** (`pyseto`) | "Platform-Agnostic Security Tokens": alternativa a JWT **sin campo de algoritmo**. Cada versión fija un único algoritmo moderno (v4: Ed25519 para firmar, XChaCha20 para cifrar). | Elimina por diseño la clase de errores de configuración de JWT. Como controlamos emisor y receptor (nuestra API), no necesitamos la interoperabilidad de JWT. |
| **Access token corto** (10-15 min) | Token que acompaña cada petición. | Si alguien lo roba, sirve poco tiempo. |
| **Refresh token con rotación y detección de reuso** | Token largo que solo sirve para pedir un access token nuevo; cada uso lo invalida y entrega otro. Se guarda **hasheado** en la BD. | Si un refresh robado se reutiliza, el servidor lo detecta y **revoca toda la familia de sesiones**. Permite "cerrar sesión en todos los dispositivos". |
| **Argon2id** (`argon2-cffi`) | Algoritmo de hash de contraseñas ganador de la Password Hashing Competition, resistente a GPUs. | Más robusto que bcrypt; recomendado por OWASP. |
| **Rate limiting** (`slowapi`) | Límite de peticiones por IP/usuario. | Frena ataques de fuerza bruta al login. |
| **RBAC** | Control de acceso por roles: `jugador`, `docente`, `admin`. | El docente puede ver progreso del grupo; solo admin edita estudiantes. |
| **Cookies HttpOnly + SameSite (solo web)** | Cookie que JavaScript no puede leer. | En la web el refresh token va en cookie HttpOnly (protege de XSS); en móvil, en `flutter_secure_storage`. |
| **Cabeceras de seguridad** | HSTS, `X-Content-Type-Options`, CSP básica. | Buenas prácticas OWASP con poco esfuerzo. |

**Fase opcional (si sobra tiempo): Passkeys / WebAuthn**
- **Qué es:** inicio de sesión **sin contraseña** con huella, cara o PIN del dispositivo (lo que usan Google, Apple, GitHub). Basado en criptografía de clave pública: el servidor nunca guarda un secreto robable.
- **Por qué es "lo más seguro":** inmune al phishing y a filtraciones de contraseñas.
- **Coste:** más complejo (dominio verificado, configuración por plataforma). Mejor como experimento al final.

**Alternativa considerada: delegar en un proveedor de identidad (Keycloak, Auth0, Supabase Auth)**
- Ventaja: seguridad "industrial" sin programarla. Desventaja: aprendes menos, añade un servicio externo y en planes gratuitos tiene límites. 🟡 Recomendación: implementarla nosotros (es una actividad para aprender).

### 4.3 Nuevas tablas (modelo de datos del juego)

```
usuarios            (id, email, hash_password, rol, alias_detective, creado_en)
sesiones_refresh    (id, usuario_id, familia_id, hash_token, expira_en, revocado_en)
progreso            (usuario_id, xp, nivel, racha_dias, actualizado_en)
casos               (id, titulo, nivel, tipo_respuesta, consulta_generadora)  ← respuestas calculadas con datos reales
intentos_caso       (id, usuario_id, caso_id, correcto, creado_en)
rescates            (id, usuario_id, estudiante_id, intervenciones, estrellas, creado_en)
insignias           (id, codigo, nombre, descripcion)
usuarios_insignias  (usuario_id, insignia_id, obtenida_en)
```
Todo con migraciones Alembic, CHECK constraints y tests como hasta ahora.

### 4.4 Otras tecnologías que suman

| Tecnología | Qué es | Para qué aquí | Prioridad |
|---|---|---|---|
| **SHAP** | Librería de explicabilidad: calcula cuánto aportó cada variable a **una** predicción concreta. | Pantalla "¿por qué?" del Laboratorio. Convierte el modelo en algo que se entiende. | ⭐ Alta |
| **WebSockets** (nativo en FastAPI) | Conexión bidireccional persistente entre app y servidor. | Duelo de cartas en tiempo real, tabla de clasificación en vivo. | Media |
| **Redis** (Upstash o Render Key Value) | Base de datos en memoria, ultrarrápida. | Caché de estadísticas, contadores del rate limit, *sorted sets* para el ranking. | Media (opcional en plan free) |
| **Docker + docker-compose** | Empaqueta API + MariaDB + Redis en contenedores. | Que cualquiera (o el profesor) levante todo con un comando. | Media |
| **GitHub Actions para la API** | CI: tests en cada push. | Ya existe para la web; replicarlo en el backend. | Media |
| **Logs estructurados** (`structlog`) | Logs en JSON con contexto (usuario, petición). | Depurar en Render sin adivinar. | Baja |

---

## 5. Arquitectura objetivo

```
                         ┌──────────────────────── app_estudiantes (Flutter) ────────────────────────┐
                         │  go_router ─► Login · Campus (Flame) · Casos · Laboratorio · Perfil       │
                         │  Riverpod (estado) · Rive (búho, medidor) · flutter_animate · fl_chart    │
                         │  ApiService + interceptor de tokens · flutter_secure_storage              │
                         └───────────────┬───────────────────────────────────┬───────────────────────┘
                                         │ HTTPS (PASETO en Authorization)   │ WSS (duelos, ranking)
                         ┌───────────────▼───────────────────────────────────▼───────────────────────┐
                         │                         api-tienda (FastAPI)                             │
                         │  /auth (registro, login, refresh, logout) ─ PASETO v4 + Argon2id + RBAC   │
                         │  /estudiantes · /estadisticas · /prediccion (+ /explicacion SHAP)         │
                         │  /juego (casos, rescates, progreso, insignias, ranking) · /ws             │
                         │  slowapi · cabeceras de seguridad · logs estructurados                    │
                         └───────────────┬───────────────────────────────────┬───────────────────────┘
                                         │ SSL                               │ (opcional)
                                ┌────────▼────────┐                 ┌────────▼────────┐
                                │  MySQL (Aiven)  │                 │ Redis (Upstash) │
                                └─────────────────┘                 └─────────────────┘
```

---

## 6. Plan de la semana (domingo 11 → sábado 17)

Cada fase termina con tests en verde, `flutter analyze`/`pytest` limpios y commit. Si una fase se alarga, **se recorta alcance, no calidad**.

| Día | Fase | Entregable verificable |
|---|---|---|
| **Dom 11** | 0 · Base | `docs/ARQUITECTURA.md` verificado contra el código. Decisiones 🟡 tomadas. Diseño del juego (bocetos de pantallas en baja fidelidad). |
| **Lun 12** | 1 · Seguridad | Tablas `usuarios` y `sesiones_refresh`, `/auth/*` con PASETO v4 + Argon2id + rotación con detección de reuso + rate limit. Tests de ataque (token alterado, refresh reutilizado, fuerza bruta). |
| **Mar 13** | 2 · Refactor Flutter | Riverpod + go_router + freezed, pantalla de login/registro, interceptor de tokens. Las 3 pestañas actuales siguen funcionando. |
| **Mié 14** | 3 · Campus | Mapa isométrico con Flame: edificios = carreras con altura y color de datos reales; tocar → expediente. |
| **Jue 15** | 4 · Laboratorio | Puzzle de rescate + endpoint SHAP + medidor y búho en Rive. |
| **Vie 16** | 5 · Casos y progresión | Casos con respuestas calculadas en el backend, XP, niveles, insignias. |
| **Sáb 17** | 6 · Pulido | Accesibilidad (reducir movimiento), sonido, capturas, README, despliegue en Render + Pages. Duelo de cartas solo si sobra tiempo. |

**Recordatorio de despliegue:** Render no tiene auto-deploy (repo conectado como público) → `Manual Deploy` después de cada push de la API. Las migraciones se aplican solas en el build.

---

## 7. Decisiones abiertas 🟡

1. **Nombre y tono del juego:** ¿"Campus Detective" u otro? ¿Mascota búho u otra?
2. **Dimensión del mapa:** ¿barrios = ciudades o pisos = semestres?
3. **Riverpod o Bloc.**
4. **¿Tiempo real (WebSockets) entra esta semana o queda para después?**
5. **¿Redis entra o se simula el ranking con SQL?** (con 300 estudiantes y pocos jugadores, SQL basta).
6. **¿Se eliminan `Producto` y `Categoria`** (legado de la primera actividad) o se dejan?

---

## 8. Prompt inicial para Claude Code

Ejecutar en `~/Proyectos/api-tienda` (y otro equivalente en `app_estudiantes`):

```text
Lee PROPUESTA_ESTUDIANTES_2.0.md. Es el plan de la semana para convertir el
proyecto en un juego educativo de datos. No hay prisa: prioriza calidad.

Fase 0 (solo análisis, no cambies código todavía):
1. Verifica la sección 1 contra el código real de este repo y genera
   docs/ARQUITECTURA.md con lo exacto: estructura de carpetas, modelos y
   tablas, endpoints con sus esquemas, dependencias con versión, cómo se
   despliega, y deuda técnica que veas. Señala cualquier diferencia con la
   sección 1 de la propuesta.
2. Revisa las secciones 4 y 5 y dime qué cambiarías: riesgos, piezas que
   sobran o faltan, y si PASETO v4 + refresh con rotación es la elección
   correcta aquí o propones algo mejor, con argumentos.
3. Propón el plan detallado de la Fase 1 (seguridad): tablas, endpoints,
   flujo de tokens, cómo se prueba cada ataque, y qué cambia en Render.

Espera mi confirmación antes de implementar.
```
