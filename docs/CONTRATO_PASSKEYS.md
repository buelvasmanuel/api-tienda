# Contrato de Passkeys (entrar sin contraseña)

> Contrato entre `api-tienda` y `app_estudiantes`. Versión **1**, 2026-10-12.
> Autenticación, tokens, errores e interceptor: [CONTRATO_AUTH.md](CONTRATO_AUTH.md).
> **Solo web por ahora** (Android, más adelante y opcional).

---

## 1. En simple

Una **passkey** es un par de claves que crea el dispositivo del jugador (el
móvil, el portátil, una llave USB). La clave privada no sale nunca del
dispositivo y se desbloquea con la huella, la cara o el PIN; la API solo
guarda la clave **pública**. Para entrar, la API manda un desafío aleatorio,
el dispositivo lo firma y la API comprueba la firma. No hay nada que robar
de la base ni que teclear en una web falsa: una passkey solo funciona en el
dominio para el que se creó.

| Pieza | Valor |
|---|---|
| Para qué | **Forma adicional de entrar**. La contraseña se conserva (y es la forma de recuperar la cuenta) |
| Dominio (RP ID) | `buelvasmanuel.github.io` |
| Origen aceptado | `https://buelvasmanuel.github.io` (la web de GitHub Pages) |
| Qué se pide al dispositivo | Passkey **descubrible** (no hace falta escribir el alias) y **verificación del usuario** (huella, cara o PIN), siempre |
| Atestación | `none`: no se comprueba el modelo del dispositivo |
| Desafíos | Aleatorios, de **un solo uso** y válidos **5 minutos**; se guardan en la base (sobreviven a un reinicio de Render) |
| Resultado del login | El mismo `TokensOut` que `POST /auth/login`: el resto de la app no cambia |
| Cuenta desactivada | Sus passkeys **no sirven** para entrar (`403 cuenta_desactivada`) |
| Restablecer la cuenta (admin) | `PUT /usuarios/{id}/password` **borra también sus passkeys** |

Las passkeys van ligadas al dominio `buelvasmanuel.github.io`: si la web se
muda a otro dominio, las passkeys existentes dejan de servir (por eso se
conserva la contraseña).

---

## 2. Registrar una passkey (con sesión iniciada)

Dos pasos: pedir las opciones y enviar lo que crea el navegador.

### 2.1 `POST /passkeys/registro/opciones` — autenticado

Sin cuerpo →
```json
{
  "reto_id": "c1a9…(32 hex)",
  "opciones": { "rp": {"id": "buelvasmanuel.github.io", "name": "Estudiantes"},
                "user": {"id": "…", "name": "Detective_Ana", "displayName": "Detective_Ana"},
                "challenge": "…", "pubKeyCredParams": […], "timeout": 60000,
                "excludeCredentials": [ … passkeys que ya tienes … ],
                "authenticatorSelection": {"residentKey": "required", "requireResidentKey": true,
                                           "userVerification": "required"},
                "attestation": "none" }
}
```
`opciones` es el JSON estándar de WebAuthn (`PublicKeyCredentialCreationOptionsJSON`).

### 2.2 `POST /passkeys/registro/verificacion` — autenticado

```json
{"reto_id": "c1a9…", "credencial": { …credential.toJSON()… }, "nombre": "Portátil de casa"}
```
`nombre` es opcional (1–60 caracteres; por defecto «Passkey»).
→ `201 PasskeyOut`.

| Error | Cuándo |
|---|---|
| `401 reto_invalido` | El `reto_id` no existe, ya se usó, caducó o es de otro usuario o de otro tipo |
| `422 passkey_invalida` | La respuesta del navegador no es válida: otro origen u otro dominio, sin verificación del usuario, desafío distinto, formato roto… |
| `409 passkey_repetida` | Esa passkey ya está registrada |
| `422` estándar | Cuerpo mal formado |

**`PasskeyOut`**
```json
{"id": 3, "nombre": "Portátil de casa", "creada_en": "…Z", "ultimo_uso": null, "sincronizada": true}
```
`sincronizada`: el proveedor de la passkey la copia entre dispositivos (p. ej.
el gestor de contraseñas de Google o Apple).

---

## 3. Entrar con una passkey (sin sesión)

### 3.1 `POST /passkeys/login/opciones` — público

Sin cuerpo →
```json
{"reto_id": "9f2e…", "opciones": {"rpId": "buelvasmanuel.github.io", "challenge": "…", "timeout": 60000,
                                   "userVerification": "required", "allowCredentials": []}}
```
`allowCredentials` va vacío: el navegador ofrece las passkeys guardadas para
ese dominio y el jugador elige (no escribe su alias).

### 3.2 `POST /passkeys/login/verificacion` — público

```json
{"reto_id": "9f2e…", "credencial": { …credential.toJSON()… }}
```
→ `200 TokensOut` (igual que `POST /auth/login`: abre una sesión nueva).

| Error | Cuándo |
|---|---|
| `401 reto_invalido` | Como en §2.2 |
| `401 passkey_invalida` | Passkey desconocida (o borrada), firma mal, otro origen u otro dominio, sin verificación del usuario, o un contador de firmas que retrocede (posible copia de la passkey) |
| `403 cuenta_desactivada` | La cuenta está desactivada |
| `429 demasiados_intentos` | Más de 60 peticiones/min desde la IP a `/passkeys/login/*` |

Todos los `401` llevan `WWW-Authenticate: Bearer`, como en CONTRATO_AUTH. **No
hay bloqueo por cuenta** (no hay nada que adivinar: sin la clave privada no se
puede firmar).

---

## 4. Gestionar tus passkeys (con sesión iniciada)

| Método y ruta | Respuesta |
|---|---|
| `GET /passkeys` | `200 list[PasskeyOut]`, la más reciente primero |
| `DELETE /passkeys/{id}` | `204`; `404 passkey_no_encontrada` si no existe o no es tuya |

---

## 5. Qué debe hacer el cliente web

Con navegadores actuales (Chrome/Edge 129+, Firefox 119+, Safari 18.4+), sin
librerías:

```js
// Registrar (con sesión):
const {reto_id, opciones} = await post("/passkeys/registro/opciones");
const cred = await navigator.credentials.create({
  publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(opciones)});
await post("/passkeys/registro/verificacion", {reto_id, credencial: cred.toJSON(), nombre});

// Entrar:
const {reto_id, opciones} = await post("/passkeys/login/opciones");
const cred = await navigator.credentials.get({
  publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(opciones)});
const tokens = await post("/passkeys/login/verificacion", {reto_id, credencial: cred.toJSON()});
```

- Desde Flutter Web se llama por interoperabilidad con JavaScript.
- Mostrar «Entrar con passkey» solo si `window.PublicKeyCredential` existe;
  si no, solo la contraseña.
- Si el usuario cancela el diálogo del navegador (`NotAllowedError`), no es un
  error de la API: volver al formulario.
- Un reto sirve una vez: ante `reto_invalido`, pedir opciones nuevas.
- Tras el login con passkey, los tokens se guardan y renuevan igual que con
  contraseña (CONTRATO_AUTH §6).

**Desarrollo en local:** los navegadores solo dejan usar passkeys en HTTPS o en
`localhost`. Para probar contra una API local, arrancarla con
`PASSKEYS_RP_ID=localhost` y `PASSKEYS_ORIGENES=http://localhost:<puerto>`.
