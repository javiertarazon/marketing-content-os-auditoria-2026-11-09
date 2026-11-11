# Auditoría técnica — Marketing Content OS

> **Estado tras las correcciones (9-nov-2026, rama `fix/auditoria-2026-11-09`).**
>
> Este informe se escribió **antes** de corregir los hallazgos. En las horas siguientes se
> aplicaron **32 de los 34**; el informe se conserva íntegro como foto del estado en el
> momento de la auditoría y este bloque dice qué cambió.
>
> | | Estado | Dónde |
> |---|---|---|
> | **AU-01 / AU-02** (publicar sin autenticación) | **Sin corregir, por decisión del usuario** | El usuario decidió el 9-nov-2026 aplazar la autenticación de la publicación hasta que todo lo demás esté cerrado. `POST /v1/jobs/{id}/approve`, `POST /v1/jobs/{id}/reject` y `POST /v1/publish` **siguen sin token**. Decisión D1 del manifiesto. |
> | Resto de P0, P1, P2 y P3 | **Corregidos y verificados** | 40 entradas `AU-01`…`AU-40` en `docs/estado-y-trazabilidad-marketing-content-os.json` |
>
> **Verificación tras las correcciones:** 131 tests en verde (antes 63), `pyflakes` sin avisos,
> `next build` en verde, `actionlint` exit 0, la instalación de la CI resuelve con `uv` y con
> `pip --require-hashes`, y el build del dashboard **falla** si se altera el contrato de la
> API (comprobado: 8 errores TS y exit 1).
>
> **Lo que no se pudo verificar:** todo lo que exige Docker (`docker compose config`, el
> arranque real de los contenedores no-root, la sintaxis de nginx). El entorno de auditoría
> no tenía Docker. Queda pendiente de revisar en la máquina de Javier.

---

# Auditoría técnica — Marketing Content OS

**Fecha:** 2026-11-09 · **Commit auditado:** `8cc4d52` (HEAD de `main`) · **Alcance:** API FastAPI, publisher-service, workers locales, dashboard Next.js, infra Docker, CI, scripts y tests.

**Método:** lectura completa del código **+ verificación por ejecución** en sandbox Linux. Se ejecutó la suite real, `pyflakes`, el build de Next.js, el smoke test del publisher, `actionlint` v1.7.12, el resolver `uv`, y se son'só la API y el publisher con `TestClient`/`curl` contra un scheduler Open-Dispatch simulado. Las cifras de este informe son medidas, no estimadas.

**Entorno:** Python 3.11.2, Node 22.19.0, npm 10.9.3. **Docker no disponible** en el sandbox → todo lo que exige `docker compose config` queda marcado como *no verificado*.

---

## 1. Veredicto

**FAIL.** El repositorio está en mejor estado que hace una semana, pero tiene **tres fallos de seguridad encadenados que permiten publicar en cuentas sociales reales sin autenticación**, **el pipeline está roto en su ruta por defecto**, y **la CI no puede ponerse en verde aunque se resuelva la facturación**.

Lo más importante: **la auditoría anterior (`docs/auditoria-2026-11.md`, 5-nov) ya no refleja el código.** Sus números están desfasados, y las correcciones que le manquent se introdujeron el 8-nov, después del último manifiesto. Este informe los cubre.

| Capa | Estado medido | Evidencia |
|---|---|---|
| Suite de tests | ✅ 63/63 pasan | `pytest -q` → `63 passed in 7.01s` |
| Lint | ✅ sin avisos | `pyflakes apps/ packages/ scripts/ tests/` → 0 avisos |
| Build del dashboard | ✅ TypeScript en verde | `next build` → exit 0, Next.js 16.4.1 |
| Smoke del publisher | ✅ OK | `scripts/smoke_publisher.py` → `SMOKE OK` |
| Notebooks | ✅ compilan | snippet exacto de la CI → ambos OK |
| Secretos en el repo | ✅ ninguno | `.gitignore` correcto, solo `.example` versionados |
| **Auth de la API** | ❌ **10 de 16 rutas abiertas** | medido con `TestClient` |
| **Publicación sin token** | ❌ **probado end-to-end** | dispatch encolado en scheduler sin credencial |
| **Pipeline (ruta por defecto)** | ❌ **roto** | `TypeError` reproducido en la etapa de investigación |
| **CI** | ❌ **0 verdes en 14 runs** | 3 bloqueadores independientes |

**34 hallazgos: 6 P0, 7 P1, 16 P2, 5 P3.**

---

## 2. Los tres bloqueadores de la CI (independientes entre sí)

El README (`docs/README` §"La CI no arranca") atribuye el bloqueo **solo** a la facturación de GitHub. Ese diagnóstico es **correcto pero incompleto**: hay dos bloqueadores más *dentro del repo* que saltarán en cuanto se pague la factura.

### 2.1 Billing (externo, ya diagnosticado por el autor)

GitHub anota literalmente: *"The job was not started because your account is locked due to a billing issue."* El job se construye (`Notebook CI` → job `test`) pero ningún runner lo arranca: `runner_id: 0`, `steps: []`, 2 s de duración. **Confirmado y correctamente diagnosticado.** No es un problema del repositorio.

### 2.2 `actionlint` se ejecuta antes del `checkout` → el job muere en el paso 1

`.github/workflows/notebook-ci.yml:28-36` valida los workflows, pero `actions/checkout` está en la **línea 38**. Cuando ese paso corre, el repo no está en disco.

```
$ actionlint .github/workflows/*.yml     # sin repo en disco
could not read ".github/workflows/*.yml": open .github/workflows/*.yml: no such file or directory
EXIT: 3

$ actionlint .github/workflows/*.yml     # repo ya clonado
EXIT: 0        ← el workflow ES válido; solo el orden está roto
```

**El workflow es válido, pero el paso de validación no puede pasar nunca como está escrito.** El job muere en el primer paso y *ningún* check posterior llega a ejecutarse: ni `pytest`, ni el smoke, ni el compose.

> Esto contradice frontalmente al README (línea 216): *"la CI ahora ejecuta `actionlint` para que un workflow inválido falle en el runner y no se descubra en el primer push real"*. Ese paso no valida nada.

Los 9 pasos restantes están correctamente después del checkout — no hay otro paso con este problema.

### 2.3 Conflicto de dependencias → `requirements are unsatisfiable`

El paso "Install dependencies" (línea 57) instala dos ficheros con restricciones **mutuamente excluyentes** de `gradio_client`:

| Fichero | Restricción |
|---|---|
| `apps/api/requirements-dev.txt:4` | `gradio_client>=1.4,<2` |
| `packages/python/adapters/requirements.txt:2` | `gradio_client>=2,<3` |
| `apps/workers/requirements-workers.txt:5` | `gradio_client>=1.0` (sin techo → resuelve a `>=2`) |

Ejecutando el comando **exacto** de la CI con `uv` (la herramienta que usa el workflow):

```
$ uv pip install --dry-run -r apps/api/requirements-dev.txt -r packages/python/adapters/requirements.txt
error: No solution found when resolving dependencies
  cause: Because you require gradio-client>=1.4,<2 and gradio-client>=2,<3,
         we can conclude that your requirements are unsatisfiable.
EXIT: 1
```

Idéntico con `pip` (`ResolutionImpossible`). **Aunque se arregle el orden de los pasos, la instalación seguirá fallando y la CI nunca llega a `pytest`.**

Detalle relevante: **nada en `apps/api` importa `gradio_client`**. Solo lo usan `packages/python/adapters/voicestudio_adapter.py:4` y `scripts/smoke_hf_spaces_cpu.py:13`. El pin de `requirements-dev.txt` es innecesario y basta con eliminarlo o alinearlo en `>=2,<3`.

### 2.4 El tercer bloqueador está oculto: el stack no es alcanzable desde el runner

`notebook-ci.yml:16-24` declara el servicio `docker:24.0-dind` con `ports: - 2375:2375`, pero **sin `--network host`**. Los puertos que publique `docker compose up` quedan en el netns del contenedor dind, **no** en el `127.0.0.1` del runner. Afecta a `curl http://localhost:8000/healthz` (línea 115), a `API_BASE_URL` (131), a `S3_ENDPOINT_URL` (133) y al Postgres (143).

*No verificado en este sandbox (no hay Docker).* El mecanismo —un sidecar dind que no comparte netns publica en su IP, no en el localhost del runner— está documentado, pero conviene confirmarlo en el primer run con facturación resuelta. Además `2375:2375` publica la API de Docker **sin TLS y en `0.0.0.0`**.

### 2.5 Estado real de los runs

**14 runs, 0 verdes.** 4 `startup_failure` (workflow inválido, ya corregido) y 10 `failure`. El último (#9, `8cc4d52`) falló en 2 s. El manifiesto solo registra hasta el run #2 (6-nov) — **faltan los 7 runs del 8-nov**.

---

## 3. P0 — Seguridad: publicar en redes sociales reales sin credencial

### 3.1 La puerta de aprobación humana no tiene autenticación

`apps/api/main.py:872` (`approve_job`) y `:949` (`reject_job`) **no exigen `Depends(require_worker_token)`**.

Medido ejecutando la app y consultando cada ruta **sin cabecera `Authorization`**:

```
METODO   RUTA                                      SIN TOKEN -> ESTADO
POST     /v1/workers/heartbeat                     401    PROTEGIDA
POST     /v1/jobs/next                             401    PROTEGIDA
POST     /v1/jobs/{job_id}/heartbeat               401    PROTEGIDA
POST     /v1/jobs/{job_id}/progress                401    PROTEGIDA
POST     /v1/jobs/{job_id}/complete                401    PROTEGIDA
POST     /v1/jobs/{job_id}/fail                    401    PROTEGIDA
POST     /v1/jobs/{job_id}/approve                 500    ABIERTA   ← no pide token
POST     /v1/jobs/{job_id}/reject                  500    ABIERTA   ← no pide token
...
TOTAL 16 rutas | CON token 6 | SIN token 10
```

Las diez rutas abiertas: `/healthz`, `/v1/workers`, `/v1/jobs` (POST y GET), `/v1/assets` (POST y GET), `/v1/jobs/{id}`, `/v1/jobs/{id}/media/{tipo}`, **`/approve` y `/reject`**.

El manifiesto dice *"8 de 14 rutas"* (P0-10). **Hoy son 10 de 16**, y las dos que se han añadido desde entonces son precisamente las que disparan la publicación.

> El commit `5179304` dice: *"La publicacion NUNCA ocurre sin pasar por esta puerta"*. La puerta existe — y está abierta. Quien alcance la API puede aprobar un video y ordenar su publicación.

### 3.2 El publisher publica sin autenticación — la puerta se puede saltar entera

`apps/publisher-service/main.py:169` — `POST /v1/publish` **no tiene ninguna dependencia de seguridad**. Y la API lo invoca sin cabecera de autenticación (`apps/api/main.py:850-854`).

**Probado end-to-end** con un scheduler Open-Dispatch simulado:

```
POST /v1/publish   (SIN NINGUNA cabecera Authorization)
{"video_url":"https://cdn.example.com/video.mp4","title":"Auditoria Mavis","platforms":["tiktok","instagram"]}
→ HTTP 200 {"unit_id":"fake-unit-123","enqueued":[{"id":"q1","target":"tiktok"},{"id":"q1","target":"instagram"}]}

LO QUE RECIBIO EL SCHEDULER:
{'category':'marketing','targets':['tiktok','instagram'],'scheduled_for':None,
 'formats':{'tiktok_post':{'video_url':'https://cdn.example.com/video.mp4',...}}}
```

El scheduler recibió la orden de publicación con el contenido completo **sin que nadie se autenticara**.

**Consecuencia:** la puerta de aprobación no es un control de seguridad, es un formality check. Basta con llamar directamente al publisher (que corre en su propio puerto) para publicar en TikTok, Instagram y YouTube **saltándose la revisión humana por completo**.

El propio repo lo reconoce en `docs/estado-sesion-actual.json:152`: *"POST /v1/jobs, /v1/assets y los nuevos /approve y /reject no exigen token. Hoy la unica proteccion es que la API escucha en 127.0.0.1 y que el worker-gateway no reenvia esas rutas."* — pero `infra/.env.production.example:1-2` instruye *"Put the API behind an HTTPS reverse proxy"*, lo que **cambia la premisa**: cualquier visitante de la URL pública podría aprobar y publicar con las credenciales de las cuentas del despliegue.

### 3.3 El pipeline está roto en su ruta por defecto: `timedelta(months=)`

`apps/workers/viral_research.py:142` mapea `"mes" → "months"`, y la línea 156 hace `timedelta(**{english: amount})`. **`timedelta` no admite `months`.**

```
TypeError: 'months' is an invalid keyword argument for __new__
```

Reproducido. La cadena: `_extract_sources_from_html` (258) → `scrape_youtube_results` (211, *fuera* del `try` que solo cubre la red) → `research_sources` → `process_research_job` → **el job se marca fallido**.

Con `Accept-Language: es-ES` (el camino por defecto, sin `YOUTUBE_API_KEY`), **cualquier resultado con más de ~1 mes revienta la etapa de investigación**. Contradice el contrato *"Nunca lanza"* de `viral_research.py:186` y `local_research_worker.py:167`.

Es decir: el recorrido "de la idea al video publicado" que documenta el commit `8cc4d52` **no es reproducible tal cual** — funcionó en esa ejecución, pero el camino de Scraping-YouTube está roto desde entonces.

### 3.4 Un solo fallo transitorio de heartbeat tira el trabajo entero

`apps/workers/worker_common.py:302-307`:

```python
except requests.RequestException:
    self._lost.set(); return     # sin reintento
```

El hilo de renovación **abandona al primer fallo**. Con `JOB_LEASE_SECONDS=300` y renovación cada 60 s, el servidor habría aceptado 2-3 renovaciones más, pero el worker se rinde: `ensure_active()` lanza y el job se marca fallido, **descartando la investigación + guion + voz ya sintetizados** (hasta 30 min de trabajo) y dejando el WAV huérfano en R2.

Reproducido por el verificador: 1 llamada al heartbeat → lease perdido → `ensure_active()` lanzó.

### 3.5 Resumen de la cadena de exposición

```
Internet → API (sin auth)  →  /approve {"publish": true}      [P0-1]
         → publisher (sin auth)  →  /v1/publish                [P0-2]
         → Open-Dispatch (con token)  →  TikTok / Instagram / YouTube
```

El token de Open-Dispatch **sí** se exige y está bien implementado (`open_dispatch_adapter.py:142-146`, `secrets.compare_digest` en la API). El problema es que **las dos puertas anteriores están abiertas**.

**Corrección recomendada (y decisión de producto, no de auditoría):** aplica un `Depends(require_worker_token)` — o un token de sesión para el dashboard — a `/approve` y `/reject`, y un token de servicio al `POST /v1/publish` del publisher, que la API debe enviar en `Authorization`. Es exactamente la decisión **D1** del manifiesto, que sigue `abierto`; lo que cambia es que ahora el coste de dejarla abierta es *publicación no autorizada*, no solo *DoS*.

---

## 4. P1

| # | Hallazgo | Evidencia |
|---|---|---|
| 4.1 | **El manifiesto y el README contradicen el estado real** | Manifiesto: `generado_en: 2026-11-05`, pero HEAD es del 8-nov. Declara *"8 de 14 rutas"* (hoy 10 de 16) y `runs_observados` termina en el run #2 — faltan 7. El README afirma que la CI ejecuta `actionlint` (línea 216), cosa que es imposible por el orden de pasos. **Incumple la regla AGENTS.md #3.** |
| 4.2 | **~2.000 líneas de workers sin un solo test** | `apps/workers/` (495+483+453+374+187 líneas) no tiene cobertura: `tests/` no importa ningún módulo de `apps/workers`. Los dos P0 anteriores habrían salido con un test de una línea. |
| 4.3 | **nginx sin rate limiting ni timeouts, expuesto a Internet** | `infra/worker-gateway.conf` (38 líneas): 0 directiva de `limit_req`, `limit_conn`, `proxy_read_timeout`, `proxy_send_timeout`, `proxy_connect_timeout`, `client_max_body_size`, `auth_request`. El gateway se publica por Quick Tunnel (`infra/docker-compose.yml:100`) sin Cloudflare Access: la única defensa es un Bearer estático que se puede forzar sin límite. |
| 4.4 | **nginx fija la IP del upstream → 502 tras recrear contenedores** | `worker-gateway.conf:9,19,29` — `proxy_pass http://api:8000` sin `resolver` ni variables: nginx resuelve una vez al cargar la config. Tras `docker compose up -d --force-recreate api` el contenedor cambia de IP y nginx sigue apuntando a la antigua. |
| 4.5 | **Diagnóstico engañoso en el publisher** | `apps/publisher-service/adapters/open_dispatch_adapter.py:396` — con `youtube` en la lista, `_stage_video` descarga el vídeo; si falla, el error se reporta como *"No se pudo conectar con el scheduler de publicacion"*. Probado: el fallo real era la descarga de `cdn.example.com`. Oculta la causa raíz. |
| 4.6 | **SSRF latente en el worker de render** | `apps/workers/local_render_worker.py:210-215` — `requests.get(audio_url)` con la URL del job sin validar esquema ni host, y el resultado se escribe en `public/voice.wav` (→ MP4 publicado). Hoy es inalcanzable (`ContentRequest` descarta campos extra), pero queda como puerta dormida. |
| 4.7 | **La API y el dashboard corren como root** | `apps/api/Dockerfile` y `apps/dashboard/Dockerfile` no declaran `USER`. Contradice `AGENTS.md:98-101` ("nadie debe correr como root"). El publisher sí lo hace bien (`USER 999:999`). |

---

## 5. P2 (selección)

- **`.dockerignore` del dashboard no excluye `.env*`** (`apps/dashboard/.dockerignore:1-3`): solo filtra `node_modules`, `.next` y `npm-debug.log*`. El contexto de build es `apps/dashboard`, así que el `.dockerignore` de la raíz no aplica → un `.env.local` con un token quedaría **horneado en una capa de la imagen publicada**.
- **Compose de producción sin healthchecks ni `restart:`** (`infra/docker-compose.yml`): ninguno de los 12 servicios declara política de reinicio; `publisher` y `worker-gateway` dependen de `api` sin `condition: service_healthy` (el compose de dev sí lo exige).
- **`init_database()` dropea y recrea el CHECK en cada arranque** (`apps/api/main.py:177-186`): `ALTER TABLE ... DROP CONSTRAINT` + `ADD CONSTRAINT` ⇒ escaneo completo de tabla y lock `ACCESS EXCLUSIVE` en cada reinicio de la API.
- **`parse_view_count` malinterpreta el formato español** (`viral_research.py:110-121`): `"1,2 M de vistas"` → `12.000.000` (real: 1.200.000). Como la selección de "virales" se ordena por `view_count`, **el criterio de selección y los datos que llegan al LLM son incorrectos**.
- **Prompt injection desde contenido web** (`local_research_worker.py:96-133`): títulos y URLs de terceros se interpolan en el prompt sin sanear; la salida del LLM se usa literalmente como guion y se publica.
- **Un `report_progress` fallido destruye el job** (`local_research_worker.py:333-355`): está dentro del `try` cuyo handler marca `voice_error`; un error de red transitorio aborta la síntesis de voz → la API responde 422 y se pierde investigación + guion.
- **Reintento infinito sin backoff ni tope** (`worker_common.py:337-348`): un `WORKER_API_TOKEN` inválido hace girar el worker para siempre, y `main()` devuelve 0 → un wrapper ve "éxito".
- **Fuga del WAV de referencia** (`local_research_worker.py:263-268`): la descarga ocurre *antes* del `try` cuyo `finally` hace el `unlink`; si `download_file` falla a medias, el audio queda en `%TEMP%` para siempre.
- **`python-multipart` con 4 vulnerabilidades `high`** en el suelo del rango (`>=0.0.12`): GHSA-5rvq-cxj2-64vf, GHSA-pp6c-gr5w-3c5g, GHSA-wp53-j4wj-2cfg, GHSA-59g5-xgcq-4qw3. En la práctica pip/uv instalan la última (0.0.33, sin vulnerabilidades), pero el rango permite las vulnerables.
- **Sin lockfile de Python**: `npm` tiene `package-lock.json`, Python no. Las instalaciones no son reproducibles.
- **Defaults inconsistentes en `docker-compose.dev.yml`** (`:6-8` vs `:69`): `postgres` usa `POSTGRES_USER:-postgres`, la `DATABASE_URL` de la API usa `:-content` → en un clon sin `.env` el rol `content` no existe y la API nunca pasa `/healthz`.
- **Imágenes con tag `latest`**: `cloudflare/cloudflared:latest`, `inovector/mixpost:latest`, `minio/mc:latest` (el resto sí está fijado).
- **`curl -s` sin `--fail`** en `scripts/generate_api_types.sh:18`: un 500 de la API se guarda como `openapi.json` y el error aparece como error de parseo.
- **`npx openapi-typescript` sin versión fijada** y sin dependencia declarada en `package.json` → descarga la última versión en cada ejecución.
- **Timeout de `subprocess.run` no mata a los nietos** (`local_render_worker.py:129-136`): mata `npx` pero no a node/Chrome; en Windows el `TemporaryDirectory` lanza `PermissionError` y **el MP4 ya renderizado se descarta**.

## 6. P3

- **Mensaje "250 MB" hardcodeado** (`apps/api/main.py:459`) mientras `MAX_ASSET_BYTES` es configurable (`settings.py:94`): si cambias el límite, el mensaje miente.
- **Código muerto**: `keep_project` nunca es `True` (`local_render_worker.py:267,384`); `duration_in_frames` se calcula y nadie lo consume (`remotion_project.py:116,186`); la rama `status_code == 204` es inalcanzable (`worker_common.py:198`).
- **`normalize_sources` descarta `views_per_hour`** (`local_research_worker.py:191-199`) → el dashboard lo pinta vacío (`page.tsx:457`).
- **`--limit` es código muerto** (`local_research_worker.py:451-456`): el operador cree que limita la búsqueda y no lo hace.
- **Tipos TS generados no se consumen** (`page.tsx` declara `Job`, `Asset` y `WorkerSession` a mano): los tipos derivarán sin aviso. Ya estaba como P2-07 en el manifiesto; sigue igual.

---

## 7. Verificado como correcto

Con evidencia, no por lectura:

- **63/63 tests pasan** (`pytest -q` → `63 passed in 7.01s`), tanto con `gradio_client 1.14.0` como con `2.7.2`.
- **`pyflakes` sin un solo aviso** en `apps/ packages/ scripts/ tests/`.
- **El dashboard compila**: `next build` → exit 0, TypeScript en verde, Next.js 16.4.1.
- **`scripts/smoke_publisher.py` → `SMOKE OK`** (dispatch, autenticación, guardas de URL e `/v1/integrations`).
- **Notebooks**: el snippet exacto de la CI los compila.
- **Cero secretos en el repo y en el historial**: `.gitignore` correcto, solo `.env.example`, `.env.colab.example` e `infra/.env.production.example` versionados, todos con placeholders. Sin `hf_`, `sk-`, `ghp_`, `AKIA`, `xox*-` ni claves PEM.
- **Los scripts shell usan `set -euo pipefail`**, no hay `eval`, los `rm -rf` usan `mktemp -d` + quoting, y ningún token se imprime en stdout.
- **Puertos atados a `127.0.0.1`** en ambos composes; nada expuesto a `0.0.0.0` en el stack desplegado.
- **El worker no usa `shell=True`**, ni `eval/exec`, ni `pickle`, ni `yaml.load`; no hay path traversal (`job_id` es UUID de la API).
- **El contrato worker↔API es correcto**: cabecera `X-Worker-Lease` coherente con el `Header(alias=…)`, cuerpos compatibles con `JobProgress`/`JobCompletion`/`JobFailure`, y el **409 "Reserva invalida" se trata bien** (el worker nunca reporta éxito tardío).
- **La guarda de origen del vídeo funciona**: el publisher rechaza con 422 cualquier `video_url` que no sea HTTPS y del host de `STORAGE_PUBLIC_BASE_URL`, **antes** de generar tráfico saliente. Es una buena defensa bien implementada.
- **El token de Open-Dispatch sí se exige** y se compara con `secrets.compare_digest`.

## 8. No verificado (limitaciones del sandbox)

- `docker compose config` para cada perfil (`publisher`, `dispatch`, `scheduler`, `colab-tunnel`) — **no verificado, no hay Docker**. Los tres ficheros YAML parsean y su interpolación y `depends_on` se revisaron a mano.
- El mecanismo dind/netns (§2.4) — **no verificado**.
- Comportamiento en Windows: `PermissionError` al borrar temporales, `npm.cmd`, PowerShell 5.1, rutas del worker de render.
- Rutas de Open-Dispatch y Mixpost contra servicios reales — solo contra un scheduler simulado.
- Publicación real en TikTok/Instagram/YouTube: no se intentó (y no procede hacerlo en una auditoría).

---

## 9. Orden de trabajo recomendado

**Bloque 1 — que la CI pueda volver a estar verde** (sin esto, nada más se verifica solo):
1. Mover el paso de `actionlint` **después** de `actions/checkout` (`notebook-ci.yml:28-36` → después de la línea 38).
2. Eliminar `gradio_client` de `apps/api/requirements-dev.txt` (nadie en `apps/api` lo usa) o alinearlo en `>=2,<3`.
3. Revisar si el servicio `dind` necesita `--network host` para que el runner alcance el stack.
4. Regularizar la facturación y relanzar con `workflow_dispatch`.

**Bloque 2 — cerrar la vía de publicación no autorizada** (decisión D1):
5. Token de servicio en `POST /v1/publish` + `Authorization` desde la API.
6. `Depends(require_worker_token)` (o token de sesión) en `/approve` y `/reject`.
7. Proxy inverso que autentique por delante antes de exponer la API a Internet.

**Bloque 3 — arreglar el pipeline**:
8. `viral_research.py:156` — tratar `"mes"` con `timedelta(days=30*amount)`, igual que ya se hace con `"año"`.
9. `worker_common.py:302-307` — reintentar el heartbeat y tolerar fallos transitorios antes de declarar el lease perdido.
10. Cubrir `apps/workers/` con tests: es la brecha que dejó pasar los dos P0.

**Bloque 4 — coherencia documental**:
11. Actualizar `docs/estado-y-trazabilidad-marketing-content-os.json` (`generado_en`, cifras de rutas, `runs_observados` con los 7 runs del 8-nov, decisiones D1 y los hallazgos nuevos).
12. Corregir la afirmación del README sobre `actionlint`.

---

*Auditoría ejecutada sobre `javiertarazon/marketing-content-os` @ `8cc4d52`. Los ficheros del repo no han sido modificados.*
