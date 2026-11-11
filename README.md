# Marketing Content OS

Plataforma modular para coordinar investigacion, generacion multimedia, renderizado y programacion de publicaciones. El repositorio contiene el orquestador para WSL, plantillas independientes para Hugging Face Spaces y notebooks para workers de Google Colab.

> MVP: API/cola, Spaces privados de voz y guiones, dos notebooks de Colab y publicación en TikTok, Instagram y YouTube mediante Open-Dispatch (MIT). Requiere configurar cuentas, claves y almacenamiento.

## Estado real de preparación end-to-end (18-oct-2026)

**El producto todavía no está validado como un generador automático de video listo para publicar.** Hay componentes funcionales probados por separado, pero no existe una evidencia reproducible de un video final hecho por el pipeline completo ni de su publicación social.

| Etapa | Estado real | Evidencia / límite |
|---|---|---|
| Dashboard, API, PostgreSQL y control local | Verificado en pruebas registradas | Build/HTTP local y ciclo API/PostgreSQL comprobados; el dashboard crea y muestra trabajos. |
| HF Qwen en CPU | Verificado por separado | Qwen3-1.7B generó salida estructurada en ~19 s en la prueba documentada. |
| TTS de catálogo OmniVoice en CPU | Verificado por separado | WAV mono 24 kHz, ~3.48 s, generado en ~39 s en la prueba documentada. Esto no demuestra clonación. |
| R2 privado | Verificado por separado | Subida, HEAD, descarga firmada temporal y limpieza de archivo de prueba. |
| Workers Colab | Bucle y panel implementados; sin conexión E2E | Los notebooks hacen polling continuo mientras se ejecutan, pero cada runtime se inicia manualmente. No hay sesión conectada ni trabajo real completado registrado. |
| Scraping → guion → voz con un trabajo del dashboard | Pendiente de prueba real | La búsqueda depende de indexación pública; no acredita métricas virales ni acceso a todas las redes. |
| Clonación de una voz propia/autorizada | Pendiente | La ruta con muestra WAV y transcripción existe; falta generar y escuchar un resultado con una muestra autorizada. |
| Render y calidad de MP4 | Pendiente | No hay MP4 final del pipeline inspeccionado en este estado. Resolución/códecs configurados no equivalen a calidad visual validada. |
| Aprobación y publicación | Publicación automatizada por el scheduler, sin aprobación en el dashboard | El `publisher-service` encola en Open-Dispatch (MIT), que publica en TikTok, Instagram y YouTube; el dashboard sigue sin guardar aprobación ni disparar la publicación al aprobar, y las cuentas OAuth de cada red todavía no están conectadas. Ver «Publicación automática». |

**Siguiente hito aceptable:** una corrida desde el dashboard que quede visible como completa, use voz de catálogo y después voz clonada autorizada, produzca un MP4 reproducible en R2 que se inspeccione visual y auditivamente, y permita una aprobación humana explícita. La publicación seguirá siendo manual hasta integrar y verificar un proveedor y sus cuentas OAuth. Colab gratuito exige arrancar el runtime manualmente y puede desconectarlo; ningún dashboard local puede garantizar que ese runtime permanezca disponible.

**Preparación actual:** componentes aislados disponibles; pipeline completo pendiente. No se asigna porcentaje de finalización porque no hay artefacto E2E aceptado que permita medirlo honestamente. Antes de exponer el API o el publisher a Internet, completar la fase de seguridad descrita en la auditoría.

Auditoría integral vigente de arquitectura, flujo, credenciales, hallazgos y plan de mejora: [docs/auditoria-integral-marketing-content-os.md](./docs/auditoria-integral-marketing-content-os.md). El [plan funcional y de cómputo](./docs/plan-funcionalidad-y-computo.md) detalla correcciones, matriz de pruebas reales, opciones para trabajar sin GPU y el orden para cerrar seguridad. La [auditoría anterior](./docs/auditoria-marketing-content-os.md) conserva las verificaciones históricas del 16 de octubre.

Para retomar el desarrollo desde el estado verificado, consulta el [manifiesto JSON de estado y trazabilidad](./docs/estado-y-trazabilidad-marketing-content-os.json): inventario de módulos, flujo, repositorios, variables de configuración (sin valores secretos), correcciones, pruebas, límites conocidos y próximos pasos para agentes.

## Estructura

- `apps/api`: API FastAPI y ciclo de vida de trabajos.
- `apps/dashboard`: dashboard Next.js con seguimiento por etapa e importación de medios locales.
- `apps/voice-studio-space`: Space Gradio privado de VoiceStudio/OmniVoice sobre ZeroGPU.
- `apps/script-generator-space`: Space Gradio privado de Qwen3-1.7B en CPU, con Qwen3-4B/ZeroGPU opcional.
- `packages/python/core`: esquemas compartidos.
- `packages/python/adapters`: clientes para servicios externos.
- `packages/node/video-templates`: punto de entrada de plantillas Remotion.
- `notebooks`: workers iniciales para investigacion y render.
- `infra`: Docker Compose para PostgreSQL y servicios locales; Redis y publisher se activan solo con perfiles opcionales.
- `scripts/clone_third_party.py`: clona los repositorios indicados solo cuando se ejecuta.

## Requisitos

- WSL 2 con Ubuntu, Git y Docker Desktop integrado con WSL.
- Docker Compose v2.
- Python 3.11+ para pruebas locales.
- Node.js 20+ para el dashboard.
- Para despliegues: cuenta Hugging Face y Google; las credenciales se configuran en cada plataforma, no en Git.

## Arranque local en WSL

Desde la raíz del repositorio:

```bash
cp .env.example .env
# Edita .env y establece contrasenas/secretos locales.
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
curl http://localhost:8000/healthz
```

API: `http://localhost:8000`; dashboard de desarrollo: `http://localhost:3001`.
Para iniciar el dashboard fuera de Docker: `cd apps/dashboard && npm install && npm run dev`.

El token `WORKER_API_TOKEN` protege las rutas de reserva, renovacion, finalizacion y fallo de los trabajos. El MVP persiste y reserva trabajos desde PostgreSQL; Redis puede arrancarse con `--profile future-queue` cuando se conecte una cola real. Publisher también es opcional (`--profile publisher`) y no participa en el flujo del dashboard. Los trabajos en ejecucion renuevan su reserva cada minuto; si Colab se desconecta, la reserva vence y el trabajo vuelve a la cola hasta un maximo de tres intentos.

### Probar workers de Colab contra la app local

Para desarrollo se puede dar a la API local un endpoint HTTPS temporal con Cloudflare Quick Tunnel, sin contratar dominio ni abrir puertos entrantes del router. El tunel es solo para pruebas: su hostname cambia al reiniciarlo, no tiene garantia de disponibilidad y el equipo local debe permanecer encendido, conectado a Internet y con Docker activo.

1. Inicia la API, el dashboard y las bases de datos:

   ```bash
   docker compose --env-file .env -f infra/docker-compose.yml up --build -d
   ```

2. Inicia la puerta de workers y el tunel:

   ```bash
   docker compose --profile colab-tunnel --env-file .env -f infra/docker-compose.yml up -d worker-gateway cloudflared
   docker compose --profile colab-tunnel --env-file .env -f infra/docker-compose.yml logs -f cloudflared
   ```

   Copia la URL `https://...trycloudflare.com` que aparece en el log. `Ctrl+C` solo cierra la vista de logs; para parar el tunel ejecuta `docker compose --profile colab-tunnel --env-file .env -f infra/docker-compose.yml stop cloudflared worker-gateway`. Al iniciar otro Quick Tunnel se generara otra URL.

3. La URL del tunel solo reenvia las operaciones worker: `POST /v1/jobs/next`, `POST /v1/jobs/{id}/heartbeat`, `POST /v1/jobs/{id}/progress`, `POST /v1/jobs/{id}/complete`, `POST /v1/jobs/{id}/fail` y `POST /v1/workers/heartbeat`. No publica el alta/listado de trabajos, el estado local de workers, la biblioteca, PostgreSQL, Mixpost ni la documentacion de la API. La API exige `WORKER_API_TOKEN` y lease para los trabajos. Copia el valor local de `WORKER_API_TOKEN` desde `.env` directamente a los Secrets de Colab; no lo pegues en el notebook ni en Git.

4. Configura almacenamiento persistente en Cloudflare R2. La API local y ambos workers necesitan credenciales S3 con permisos solo para ese bucket. Guarda las credenciales en `.env` local y en Colab Secrets:

   - `S3_ENDPOINT_URL`: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
   - `S3_BUCKET`: nombre del bucket R2.
   - `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY`: credenciales S3 del bucket.

   Habilita el **acceso público `r2.dev`** del bucket. Es un requisito del publicador: Open-Dispatch entrega a TikTok e Instagram una URL HTTPS desde la que descargar el vídeo, y el publicador sólo acepta URLs cuyo host sea el de `STORAGE_PUBLIC_BASE_URL`. El acceso público de R2 es **por clave, no por listado**: `GET https://pub-<cuenta>.r2.dev/` devuelve 404 y sólo se puede descargar un objeto si se conoce su clave completa. Los workers siguen accediendo por credenciales S3 y el dashboard sigue sirviendo la biblioteca con enlaces firmados de cinco minutos.

   Verificado en este entorno (bucket `marketing-content-media`): `GET https://pub-<cuenta>.r2.dev/<clave>` responde **200** con User-Agent de navegador, de Instagram y de TikTok, y **403 `error code: 1010`** con el User-Agent por defecto de Python (`Python-urllib`). Cloudflare bloquea esa firma concreta; no es que el objeto sea privado. Si una plataforma no descarga el vídeo, comprueba el User-Agent y no el estado del bucket.

   Para administrar R2 mediante la REST API desde este equipo, crea un token separado de las credenciales S3. En Cloudflare abre **My Profile > API Tokens > Create Token > Create Custom Token** y configura:

   - **Permissions**: `Account` → `Workers R2 Storage` → `Edit`. La API documenta este permiso como `Workers R2 Storage Write`; `Edit` concede operaciones de lectura/listado y escritura, incluida la configuración del bucket.
   - **Account Resources**: `Include` → `Specific account` → selecciona la cuenta que contiene el bucket.
   - No agregues permisos de Zone/User ni uses `Global API Key`. Puedes establecer una expiración corta para una tarea puntual, pero el token debe seguir activo durante la prueba.

   Guarda el valor secreto del token en `CLOUDFLARE_API_TOKEN`, el identificador de cuenta en `CLOUDFLARE_ACCOUNT_ID` y el nombre en `R2_BUCKET_NAME`, dentro de `.env.cloudflare.local` (archivo ignorado por Git). El campo `CLOUDFLARE_API_TOKEN` recibe el secreto del token Bearer, no su nombre ni el ID mostrado en el panel.

   **Son credenciales distintas:** el token Bearer administra recursos por la REST API; `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY` son credenciales compatibles con S3 para leer/escribir objetos y van en `.env`/Secrets de Colab, con permisos `Object Read & Write` limitados al bucket. No uses `Admin Read & Write` para el acceso S3 de los workers.

   El token REST de Cloudflare solo se requiere para tareas de administración; la API y los workers usan las credenciales S3. Nunca pegues tokens ni claves en el chat, README, notebooks o repositorio.

5. En el dashboard local, ve a **Biblioteca local de medios** y selecciona la carpeta de recursos. WAV, MP4, JPG, PNG y WEBP (hasta 250 MB por archivo) se importan al bucket R2, con clave propia y accesibles por URL pública (eso es lo que necesita el publicador para entregar el vídeo a TikTok e Instagram). No se comparte ninguna ruta del disco con Colab; los workers descargan los objetos desde R2. Para una voz clonada, selecciona la muestra WAV, escribe su transcripción exacta y confirma que eres titular o tienes autorización. OmniVoice usará esa referencia para narrar el guion nuevo. Para editar con material propio, selecciona un fondo MP4 o una imagen; se conserva el video, pero se silencia su audio original para priorizar la narración.

6. En **cada notebook** abre el panel **Secrets** (icono de llave), crea cada nombre, pega su valor y habilita el acceso para el notebook. Usa exactamente:

   La plantilla local [`.env.colab.example`](./.env.colab.example) reúne las variables requeridas por los workers. Puedes copiarla a `.env.colab` para preparar tus valores localmente; esa copia queda ignorada por Git. Colab no importa el archivo automáticamente: transfiere cada valor directamente al panel **Secrets** del notebook. No subas `.env.colab` a Drive, al repositorio ni al chat. `API_BASE_URL` debe ser la URL HTTPS del Quick Tunnel activo, sin `/` final; cambia al reiniciar el túnel.

   - En `01_research_scrapegraph.ipynb`: `API_BASE_URL` (la URL `trycloudflare.com`, sin `/` final), `WORKER_API_TOKEN`, `SCRIPT_SPACE_URL` (`https://javiertarazon-marketing-script-generator.hf.space`), `VOICE_STUDIO_URL` (`https://javiertarazon-voice-studio-agent.hf.space`), `HF_TOKEN`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY`. Opcional: `YOUTUBE_API_KEY` y `YOUTUBE_REGION_CODE` para ordenar videos recientes de YouTube por vistas por hora; sin esa clave la búsqueda usa el índice web y no puede afirmar tendencias cuantitativas.
   - En `02_render_remotion.ipynb`: `API_BASE_URL`, `WORKER_API_TOKEN`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY`.

   Crea `HF_TOKEN` en Hugging Face como token de solo lectura que tenga acceso a tus dos Spaces privados. No compartas ese token en el chat. Los Spaces ya desplegados son privados y usan ZeroGPU, que puede poner las solicitudes en cola y limitar la cuota diaria.

7. Abre ambos notebooks en Google Colab, guarda sus Secrets y ejecuta sus celdas desde arriba. Deja ejecutándose la última celda de cada notebook: ahora registran su presencia en la API y consultan continuamente la cola cada 20 segundos. El dashboard muestra cada worker como conectado, ocupado o desconectado. Al detener la celda se registra la desconexión; si Colab cierra el runtime abruptamente, el dashboard la detecta tras 90 segundos sin señal. Los trabajos permanecen en PostgreSQL y se reencolan al vencer el lease si el worker cayó. Las sesiones gratuitas pueden desconectarse, por lo que tendrás que volver a iniciar el notebook tras una interrupción. Si reinicias el túnel, actualiza `API_BASE_URL` en Secrets y vuelve a ejecutar la configuración.

8. Abre `http://localhost:3001`, crea un trabajo con tema, selecciona las redes y, opcionalmente, agrega una URL de referencia o una voz/fondo propio. El worker busca páginas públicas indexadas con ScrapeGraphAI y DuckDuckGo; no requiere credenciales de esas redes. El dashboard muestra etapa, porcentaje, fuentes extraídas y el resultado o bloqueo por plataforma. La cobertura depende de la indexación y del acceso anónimo permitido por cada sitio; no se evaden inicios de sesión, CAPTCHA ni controles. Al completar investigación y voz, la API crea el trabajo de render. El MP4 final se obtiene mediante un enlace firmado temporal.

9. Al terminar, detene las celdas de Colab con el boton Stop y apaga el tunel con el comando del paso 2. No apagues Docker mientras haya trabajos activos. Para produccion/migracion, se conserva el mismo API, esquema y almacenamiento S3; se cambia el endpoint temporal por un dominio fijo con HTTPS y se despliegan API y base de datos en el VPS.

### Publicación automática: Open-Dispatch (MIT)

El `publisher-service` (`apps/publisher-service`) encola las publicaciones contra **Open-Dispatch** (<https://github.com/Matthew-Selvam/Open-Dispatch>), un scheduler open source con licencia **MIT** que publica en **TikTok, Instagram y YouTube** sin coste y con API propia. Es el scheduler que usa el proyecto.

Se eligió tras comparar licencia, coste y cobertura de plataformas (ver [`docs/opciones-publicacion-gratuita.md`](./docs/opciones-publicacion-gratuita.md)): Mixpost Lite no incluye API REST, Postiz es AGPL y exige Temporal, y `postiz-cheap` depende de MuAPI, que es de pago.

```bash
# 1) En .env, el token Bearer que exigirá Open-Dispatch (el mismo valor para el
#    publicador y para el scheduler):
#    OPEN_DISPATCH_BASE_URL=http://localhost:8010
#    OPEN_DISPATCH_API_TOKEN=<token largo y aleatorio>
#    OPEN_DISPATCH_MEDIA_DIR=/media
# 2) Credenciales de cada red (las lee el worker de Open-Dispatch):
#    TIKTOK_ACCESS_TOKEN=...   IG_USER_ID=...   IG_TOKEN=...
#    YOUTUBE_CLIENT_ID=...   YOUTUBE_CLIENT_SECRET=...   YOUTUBE_REFRESH_TOKEN=...
# 3) Levanta Open-Dispatch, su worker y el publicador:
docker compose --profile publisher --profile dispatch --env-file .env -f infra/docker-compose.yml up -d --build
```

El panel de Open-Dispatch queda en `http://localhost:8010` y su cola muestra el estado de cada publicación (`queued`, `publishing`, `published`, `failed`, `dead`, `canceled`).

**Contrato que usa el publicador** (verificado contra el código de Open-Dispatch, commit `773dd54`):

- `POST /dispatch` responde `202` con `unit_id` y `enqueued`. Autenticación: `Authorization: Bearer $OPEN_DISPATCH_API_TOKEN`.
- TikTok e Instagram (`tiktok_post`, `instagram_post`) descargan el vídeo de una **URL pública**: por eso `STORAGE_PUBLIC_BASE_URL` debe apuntar al host público del mismo bucket R2.
- YouTube (`youtube_short`) sube el archivo **desde disco**: `video_path` debe ser una ruta relativa sin `..` bajo `OPEN_DISPATCH_MEDIA_DIR`. El publicador descarga el MP4 al volumen `open_dispatch_media` (montado en `/media` en los tres servicios) y envía esa ruta.
- Un solo dispatch puede llevar varias redes: `platforms: ["tiktok", "instagram", "youtube"]`, o `plataforma:cuenta`.

El publicador solo acepta vídeos HTTPS del host de `STORAGE_PUBLIC_BASE_URL`; cualquier otra URL se rechaza con 422 **antes** de generar tráfico. `GET /v1/integrations` devuelve las plataformas publicables con la configuración actual.

**Limitación conocida de la cola.** Sin `DATABASE_URL` ni `REDIS_URL`, Open-Dispatch usa su backend JSONL, que su propio código documenta como «single-process»: el cerrojo es un `threading.Lock` y no protege entre el proceso de la API y el del worker. Es el despliegue de referencia de upstream y funciona en uso normal, pero para carga alta conviene extender la imagen con `psycopg2-binary` y definir `DATABASE_URL`: el backend de Postgres usa `SELECT ... FOR UPDATE SKIP LOCKED` sobre la tabla `open_dispatch_queue` y sí admite varios procesos.

### Mixpost queda como scheduler alternativo

El adaptador de Mixpost sigue en el repo y se activa con `PUBLISHER_BACKEND=mixpost`, pero **su API REST solo existe en Mixpost Pro (pago único de USD 299) o en Mixpost Cloud**: la edición Lite (MIT, gratuita) no la incluye, ni MCP, ni webhooks, según <https://mixpost.app/pricing>. Para levantarlo junto con sus servicios aislados de MySQL y Redis:

```bash
docker compose --profile scheduler --env-file .env -f infra/docker-compose.yml up -d mixpost
```

Abre `http://localhost:3002/mixpost` para completar la configuración inicial de Mixpost. El dashboard permite abrir Mixpost y descargar el MP4 renderizado para subirlo manualmente y programarlo. La imagen `inovector/mixpost:latest` y MySQL necesitan descargarse la primera vez.

**El Mixpost local solo sirve para evaluar la interfaz.** Las conexiones OAuth de redes sociales normalmente necesitan una URL pública HTTPS, un dominio estable y credenciales de desarrollador aprobadas por cada plataforma. Configura `MIXPOST_URL` y `MIXPOST_DOMAIN` con ese dominio antes de conectar cuentas; no expongas la instancia local directamente a Internet.

### Publicación automática: qué edición de Mixpost necesitas

El `publisher-service` (`apps/publisher-service`) publica vídeos con la API REST de Mixpost. **Esa API sólo existe en Mixpost Pro (pago único de USD 299) y en Mixpost Cloud**; la edición **Lite (MIT, gratuita) no incluye la API**, ni MCP, ni webhooks, según la tabla de features de <https://mixpost.app/pricing>. Lite sí publica en Facebook Pages, X y Mastodon; Instagram, TikTok y YouTube requieren Pro.

| Edición | API REST | Publica en |
|---|---|---|
| Lite (gratis) | ❌ | Facebook Pages, X, Mastodon |
| Pro (USD 299 único) | ✅ | todas las plataformas |
| Cloud (suscripción) | ✅ | todas las plataformas |

Con Lite, la vía soportada es la que ya describe este README: descarga el MP4 del dashboard y prográmalo a mano en la UI de Mixpost. Con Pro o Cloud, el flujo automático es:

```bash
# 1) Crear una clave personal en la pagina API del panel de Mixpost (se muestra una sola vez).
# 2) Ponla en .env:
#    MIXPOST_BASE_URL=http://localhost:3002/mixpost/api
#    MIXPOST_API_KEY=<clave personal>
#    MIXPOST_WORKSPACE_UUID=   # vacio = se resuelve el primero del token
# 3) Levanta Mixpost y el publicador:
docker compose --profile publisher --env-file .env -f infra/docker-compose.yml up -d mixpost publisher
```

El publicador usa la API real de Mixpost (`GET /workspaces`, `GET /{workspace}/accounts`, `POST /{workspace}/media`, `POST /{workspace}/posts`), **no** la API de Postiz: Mixpost tiene su propio contrato (`accounts` + `versions`, medios referenciados por id numérico, `Authorization: Bearer <clave>`). `MIXPOST_APP_KEY` es la clave de cifrado de Laravel y sólo la usa el contenedor de Mixpost; la credencial del publicador es `MIXPOST_API_KEY`. Con Lite, la vía soportada es la que ya describe este README: descarga el MP4 del dashboard y prográmalo a mano en la UI.

Mixpost Lite sirve la API y la UI bajo el prefijo `/mixpost` (`MIXPOST_CORE_PATH`), por eso las URLs terminan en `/mixpost/api`.


### La CI no arranca: la cuenta de GitHub está bloqueada por facturación

Este repositorio es **público** desde el 6-nov-2026 (antes de hacerlo se auditó el historial completo: 79 blobs, cero secretos, cero firmas de credenciales). Aun así, la CI no ejecuta ni un paso, y GitHub lo dice explícitamente en la anotación del job:

> **The job was not started because your account is locked due to a billing issue.**

El workflow **es válido** (`actionlint v1.7.12` pasa sin avisos) y **se reconoce y construye el job** (`Notebook CI` → job `test`), pero ningún runner lo arranca: `runner_id: 0`, `steps: []` y 2 s de duración total. Eso no es un problema del repositorio: es la **cuenta bloqueada por saldo impagado**, y arrastra a Actions entero con independencia de la visibilidad del repo.

Qué hacer:

1. **Settings → Billing and plans → Payment information**: pagar la factura pendiente o corregir la tarjeta que falló. GitHub bloquea la cuenta completa cuando queda saldo sin pagar.
2. Comprobar en <https://github.com/settings/billing> que la cuenta ya no figura como bloqueada y que no queda ninguna factura vencida.
3. En cuanto se regularice, relanzar la CI desde la pestaña **Actions**: el workflow tiene `workflow_dispatch` para eso, sin commits de prueba.
4. Si el pago está al día y la cuenta sigue bloqueada, ticket a soporte indicando la anotación *«your account is locked due to a billing issue»* y el run `42204204757`.

Cómo se llegó al diagnóstico, por si hay que repetirlo: con el repositorio todavía privado, los runs morían con `workflow name` vacío, `path` `BuildFailed`, `conclusion` `startup_failure`, `jobs: 0` y sin logs. Eso se reprodujo **igual** con un workflow mínimo de tres líneas, lo que descartó YAML, runner, secrets y reusable workflows; `actionlint` confirmó que el fichero era válido. Al pasarlo a público, GitHub pasó a reconocer el workflow y a construir el job, y la anotación del check-run dio la causa exacta.

Este repo sí tenía un bug real de workflow, heredado del original: `privileged: true` no es una clave válida dentro de `services:` (las válidas son `credentials`, `env`, `image`, `options`, `ports`, `volumes`), así que su CI nunca llegó a ejecutar. Corregido a `options: --tls=false --privileged`, y la CI ahora ejecuta `actionlint` para que un workflow inválido falle en el runner y no se descubra en el primer push real.

## Progreso del pipeline y medios locales

La API persiste `progress.stage`, `progress.message` y `progress.percent` en cada trabajo. Los workers Colab actualizan esos datos con un endpoint protegido por el token del worker y su lease, y envían una señal de presencia mientras esperan o trabajan. El dashboard consulta trabajos y sesiones cada 10 segundos y muestra el estado, la etapa y el trabajo activo de cada worker. Una investigación completada genera automáticamente un trabajo separado de render. La app local controla la cola y el estado, pero no puede iniciar una sesión gratuita de Colab: cada notebook debe abrirse y ejecutarse una vez, y reiniciarse si Colab la desconecta.

El dashboard permite seleccionar una carpeta desde el navegador e importar WAV, MP4, JPG, PNG y WEBP al bucket R2 configurado. La API registra sus metadatos en PostgreSQL y sube el archivo con las credenciales S3 locales. El worker de investigación usa una muestra WAV con transcripción para clonación zero-shot; el de render usa un MP4 o una imagen seleccionados como fondo. Los objetos quedan privados y los notebooks reciben claves internas, no enlaces públicos. Límite actual de carga: 250 MB por archivo.

Para habilitar la biblioteca local, completa `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY` en `.env`, con permisos de lectura/escritura de objetos en el bucket. En Colab repite esas cuatro variables en Secrets. La API está ligada a `127.0.0.1`; el worker gateway no expone la biblioteca ni los endpoints del dashboard. Las operaciones de clonación deben usar una voz propia o con autorización; el formulario solicita la transcripción exacta y una confirmación.

Para apagar los servicios locales: `docker compose --profile dispatch --profile scheduler --env-file .env -f infra/docker-compose.yml down`. Los datos de PostgreSQL, Open-Dispatch y Mixpost se mantienen en volúmenes Docker.

## Pipeline completo en local (sin Colab ni túnel)

Los dos workers de Colab tienen equivalentes locales en `apps/workers/` que se ejecutan como procesos de Python en Windows. Así el pipeline entero se puede probar en esta máquina, sin abrir Colab y sin levantar el túnel de Cloudflare: los Spaces de Hugging Face remotos siguen generando el guion y la voz.

Requisitos: `ffmpeg` y `ffprobe` en el PATH (`winget install Gyan.FFmpeg`), Node.js con `npm.cmd`, un `HF_TOKEN` con acceso a los dos Spaces privados, y la pila de Docker levantada.

```powershell
# 1) Levantar la pila (Postgres, API, dashboard, publicador, Open-Dispatch)
docker compose --profile dispatch --env-file .env -f infra/docker-compose.yml up -d --build

# 2) Research: busca los vídeos más virales del nicho, genera el guion en el
#    Space de Qwen, la voz en el de OmniVoice y sube el WAV a R2.
#    Al completarse, la API encola sola el trabajo de render.
python apps\workers\local_research_worker.py --once --topic "finanzas personales" --create-job --verbose

# 3) Render: descarga el WAV de R2, monta el proyecto Remotion (MarketingShort,
#    1080x1920@30), renderiza con ffmpeg y sube el MP4 a R2.
python apps\workers\local_render_worker.py --once --verbose
```

La búsqueda de vídeos virales funciona **sin `YOUTUBE_API_KEY`**: el extractor lee la página de resultados de YouTube, ordena por número de vistas y devuelve los más vistos. Si la clave está definida, se usa la YouTube Data API oficial con `order=viewCount`, que es más precisa.

Los workers locales tienen en cuenta tres diferencias con los notebooks: `API_BASE_URL` admite `http://` (en Colab tiene que ser `https://` porque la salida es a Internet), no se usa `--browser-executable=/usr/bin/chromium` (ruta de Linux) y las rutas `/content` se sustituyen por un directorio temporal escribible.

## La puerta de aprobación: el video se publica solo si alguien lo aprueba

El render **no** termina en «completado». Cuando el MP4 está listo, el trabajo pasa al estado `awaiting_approval` y se queda ahí. Hasta que una persona lo aprueba, no se publica nada.

| Estado | Significado |
| --- | --- |
| `awaiting_approval` | El video está renderizado y esperando revisión. |
| `approved` | Alguien lo ha aprobado. La publicación se ha intentado. |
| `rejected` | Alguien lo ha rechazado. No se publica y queda el motivo. |

En el dashboard, el video aparece con los botones **Aprobar y publicar** y **Rechazar**, y con el resultado de la publicación (unidades encoladas en el scheduler o el error).

Por API:

```powershell
# Aprobar y publicar en las plataformas del trabajo original
curl.exe -X POST http://127.0.0.1:8000/v1/jobs/<job_id>/approve -d "{\"publish\": true}" -H "Content-Type: application/json"

# Aprobar sin publicar (para revisarlo por otro canal antes)
curl.exe -X POST http://127.0.0.1:8000/v1/jobs/<job_id>/approve -d "{\"publish\": false}" -H "Content-Type: application/json"

# Rechazar
curl.exe -X POST http://127.0.0.1:8000/v1/jobs/<job_id>/reject -d "{\"reason\": \"el gancho no convence\"}" -H "Content-Type: application/json"
```

Al aprobar, la API construye la URL pública del MP4 a partir de `STORAGE_PUBLIC_BASE_URL` y la encola en el scheduler llamando a `publisher-service` (`PUBLISHER_BASE_URL`). Si el scheduler está caído, la aprobación **sigue siendo válida**: el error queda en `output.publication.error` y volver a aprobar reintenta la publicación. Aprobar un trabajo ya aprobado vuelve a encolarlo; aprobar uno rechazado devuelve `409`.

> La API no autentica estas rutas, igual que `POST /v1/jobs` o `POST /v1/assets`. Hoy la protección es que la API escucha solo en `127.0.0.1` y que el worker-gateway no reenvía estas rutas. Queda dentro de la decisión pendiente **D1** (modelo de autenticación).

## Hugging Face Spaces

Clona primero VoiceStudio con `python scripts/clone_third_party.py`. Instala la CLI `hf` en WSL (`uv tool install huggingface_hub`) e inicia sesion (`hf auth login`) con un token que permita crear Spaces y subir archivos. La cuenta debe ser elegible para ZeroGPU; las cuentas gratuitas requieren correo verificado y mas de 30 dias de antiguedad, y tienen cuota diaria. Ejecuta `bash scripts/deploy_hf_spaces.sh`: crea/actualiza `voice-studio-agent` como Space privado Gradio con hardware `zero-a10g` (el identificador de ZeroGPU en la CLI actual) y publica la app. No se selecciona T4 ni hardware facturable. Tras publicar, `hf spaces zero-gpu quota` consulta el saldo diario.

El Space carga `k2-fsa/OmniVoice` de forma diferida en CPU por defecto (máximo dos hilos), así las llamadas no solicitan ZeroGPU ni gastan esa cuota. El endpoint Gradio `/predict` recibe texto y atributos de voz, y opcionalmente una muestra WAV más su transcripción exacta para clonación zero-shot. La CPU puede tardar minutos por una narración; el notebook ahora tolera esperas largas. Para volver a GPU se debe configurar `USE_ZEROGPU=1` y se consumirá la cuota compartida. La clonación con una muestra propia y el flujo Colab aún requieren prueba end-to-end.

Para generar investigación editorial y guiones, `apps/script-generator-space` usa `Qwen/Qwen3-1.7B` en CPU por defecto, con dos hilos; la GPU opcional (`USE_ZEROGPU=1`) usa Qwen3-4B y consume cuota. Inicia sesión con `hf auth login` y ejecuta `bash scripts/deploy_hf_script_space.sh`; el script crea/actualiza el Space privado `marketing-script-generator` en ZeroGPU. Configura `SCRIPT_SPACE_URL` y guarda un token HF de solo lectura en el secreto `HF_TOKEN` de Colab. El Space usa el mismo token que el Space de voz. Revisa el build y la cuota antes de producción; el despliegue no se ejecuta automáticamente.

En la cuenta `javiertarazon` están publicados los Spaces privados [Marketing Script Generator](https://huggingface.co/spaces/javiertarazon/marketing-script-generator) y [VoiceStudio](https://huggingface.co/spaces/javiertarazon/voice-studio-agent). La ruta CPU nueva pasó pruebas autenticadas el 18-oct-2026: guion Qwen en 19 s y voz WAV en 39 s; `USE_ZEROGPU=0` quedó fijado en ambos y el saldo se mantuvo en 265.64/300 GPU-segundos antes y después. El token OAuth de `hf auth login` está en la caché de WSL, pero `HF_TOKEN` sigue vacío en `.env`. Para ejecutar el notebook agrega a Colab Secrets un token HF de lectura con acceso a ambos Spaces. Colab no puede leer la caché local de WSL; no pegues tokens en celdas ni en el repositorio.

ZeroGPU gratuito tiene cuota diaria y colas, no es inferencia ilimitada ni un SLA. Las condiciones de elegibilidad y cuota pueden cambiar; verifica la disponibilidad desde tu cuenta Hugging Face. El worker usa la biblioteca open-source ScrapeGraphAI (`search_on_web`) para buscar con DuckDuckGo páginas públicas de las redes elegidas; descarga las páginas accesibles con `requests` y BeautifulSoup. Si una página no es legible, puede utilizar únicamente el fragmento público que devuelve el índice de DuckDuckGo y lo etiqueta como tal en el dashboard. Esta búsqueda no usa APIs ni credenciales de YouTube, Reddit, X, Instagram, TikTok o Facebook. Puede omitir publicaciones no indexadas, devolver resultados antiguos, encontrar perfiles/páginas de negocio en lugar de publicaciones o no acceder a páginas dinámicas/privadas; no equivale a escucha social en tiempo real ni a métricas de tendencias. Los estados por plataforma muestran páginas extraídas, fragmentos, ausencia de contenido legible o errores. El Space Qwen recibe el texto recopilado y produce notas y guion en una inferencia; no garantiza exactitud factual, así que revisa fuentes y guion antes de publicar. No se usa OpenAI.

No publico HyperFrames ni Open-Generative-AI como Spaces Gradio falsos: ambos repos son frameworks/apps, no inferencia Gradio. HyperFrames/Remotion se usan en Colab para render; Open-Generative-AI es una app web/Electron independiente.

## Google Colab

Abre `notebooks/01_research_scrapegraph.ipynb` o `notebooks/02_render_remotion.ipynb` en Colab con la cuenta `javiertarazpon@gmail.com` (sube los archivos desde tu equipo o clona este repositorio despues de publicarlo en GitHub). Este proyecto no recibe acceso a tu Drive ni a tu cuenta. Guarda secretos en **Secrets** de Colab, no en celdas compartidas. Ambos notebooks necesitan `API_BASE_URL`, `WORKER_API_TOKEN`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY`. El notebook de investigación también necesita `SCRIPT_SPACE_URL`, `VOICE_STUDIO_URL` y un `HF_TOKEN` de solo lectura con acceso a ambos Spaces privados. Investiga y genera el guion con Qwen3-1.7B en CPU por defecto, llama a VoiceStudio por Gradio con la opción de usar una muestra de voz, sube WAV a R2 privado y encola el render; el segundo notebook descarga el audio y los medios propios para componer el MP4.

Los notebooks mantienen workers de polling mientras sus celdas y runtimes sigan activos. Un Quick Tunnel permite que Colab consulte durante pruebas la API propia de WSL mediante HTTPS temporal; no mantiene el equipo despierto ni elude las restricciones/cuotas de Colab.

El notebook de investigación requiere `API_BASE_URL`, `WORKER_API_TOKEN`, `SCRIPT_SPACE_URL`, `VOICE_STUDIO_URL`, `HF_TOKEN` y credenciales S3/R2 para el resto del pipeline. La URL de referencia es opcional: con ella se analiza esa página y las redes seleccionadas; sin ella, se buscan las plataformas elegidas (o todas si no se selecciona ninguna). La búsqueda social pública usa ScrapeGraphAI con DuckDuckGo, sin claves de plataforma, e informa resultados, bloqueos y errores por plataforma en el dashboard. Esto no garantiza publicaciones recientes ni acceso anónimo a todas las redes. El notebook de render requiere un proyecto Remotion, configuración S3/R2 y el mismo API público. Remotion y HyperFrames son frameworks de render, no Spaces de generación de imágenes; el pipeline necesita una composición/plantilla real.

## Pruebas

```bash
export PATH="$HOME/.local/bin:$PATH"
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r apps/api/requirements-dev.txt -r packages/python/adapters/requirements.txt
python -m pytest tests
```

## Repositorios externos

`python scripts/clone_third_party.py` clona shallow en WSL los repositorios indicados, excepto `MoneyPrinterTurbo` (excluido a petición); omite clones existentes y no actualiza ni sobrescribe destinos previos. No hace falta clonar esos repositorios completos en Colab: cada notebook instala sus dependencias en el runtime. Al publicar el Space de voz, el script empaqueta solo el módulo `omnivoice` y su licencia desde el clon local de VoiceStudio, evitando instalar las dependencias de escritorio/ASR que este MVP no usa. `marketingskills` aporta instrucciones, no una librería runtime. HyperFrames, OpenMontage y Open-Generative-AI quedan clonados como fuentes, pero no son dependencias activas de este MVP. Revisa licencias antes de uso comercial: VoiceStudio es AGPL-3.0, los modelos Qwen3 son Apache-2.0 y Remotion tiene condiciones de licencia comercial.

## Despliegue y seguridad

No se crean recursos de Hugging Face/Colab automáticamente: el despliegue requiere iniciar sesión y configurar tokens y secretos en las cuentas correspondientes. Antes de producción, configura TLS, secretos fuertes, autenticación de usuarios, almacenamiento S3/R2, migraciones de base de datos, límites de uso y políticas de acceso para Mixpost y redes sociales.
