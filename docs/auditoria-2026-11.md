# Auditoría técnica y trazabilidad — Marketing Content OS

**Generado:** 2026-11-05 · **Alcance:** backend FastAPI, workers de Colab, HF Spaces, dashboard Next.js, publisher-service, infraestructura, CI, scripts y tests.
**Método:** lectura completa del código + verificación por ejecución (import real, `pytest`, `docker compose config`, `pyflakes`, `git fsck`). El detalle estructurado de cada hallazgo está en `docs/estado-y-trazabilidad-marketing-content-os.json`.

---

> **Actualización de 5-nov-2026 (posterior a esta auditoría):** el publisher ya **no** habla Mixpost. Se reescribió contra **Open-Dispatch** (MIT), que publica en TikTok, Instagram y YouTube con API propia y sin coste; la API REST de Mixpost Lite (gratis) no existe. El adaptador de Mixpost se conserva como alternativa (`PUBLISHER_BACKEND=mixpost`).
>
> Estado vigente, contrato verificado y pasos pendientes: `docs/estado-y-trazabilidad-marketing-content-os.json` (sección `scheduler_open_dispatch`) y la sección «Publicación automática» del README. Este documento se conserva como foto del estado en el momento de la auditoría.

## 1. De dónde viene el proyecto

El repositorio estaba **vacío**: sin `HEAD`, `config` ni `refs` en `.git`, sólo 258 objetos sueltos. Se reconstruyó el árbol del commit más reciente y se recuperaron **71 archivos**, preservando los **12 commits** originales (`be2c449` → `e0acb49`, 14–21 oct 2026).

Git bloqueaba el acceso por *dubious ownership*: el directorio pertenece al SID de la máquina original y el usuario actual es otro. Resuelto con `git config --global --add safe.directory`.

**Pendiente de tu lado:** reconstruir el `.git` de forma permanente (commit + push) y registrar credenciales de GitHub (no hay `gh` ni entradas en el gestor de credenciales).

---

## 2. Veredicto

El proyecto **no arrancaba en ninguna de sus capas**. Hoy: API importable con 31/31 tests verdes, imagen Docker correcta, workers compilables, stack de desarrollo levantable, CI coherente y publisher hablando el protocolo real de Mixpost.

| Capa | Antes | Ahora |
|---|---|---|
| API FastAPI | ❌ `NameError` al importar | ✅ 31/31 tests |
| Imagen Docker API | ❌ `ModuleNotFoundError: settings` | ✅ incluye `settings.py` + `pydantic-settings` |
| Worker de investigación | ❌ `NameError: re` tras gastar búsqueda + LLM | ✅ compila |
| Stack de desarrollo | ❌ build fallaba, comando y volúmenes rotos | ✅ `docker compose config` valida |
| CI | ❌ fallaba siempre (4 causas) | ✅ pasos reales de pytest/build/smoke |
| Publisher → Mixpost | ❌ hablaba la API de Postiz | ✅ reescrito contra la API real |
| Auth de la API | ❌ 8 de 14 rutas públicas | ⚠️ **decisión D1 pendiente** |

**34 hallazgos documentados**: 13 P0, 10 P1, 8 P2, 3 P3. Corregidos y verificados: 22.

---

## 3. Los cinco bloqueadores que tumbaban el arranque

1. **`apps/api/main.py:32`** — `settings = get_settings()` con `get_settings` nunca importado. Mataba el import de la app *y* la recolección de pytest (suite interrumpida, 0 tests). Además `settings` ligaba el **módulo**, no la instancia.
2. **`apps/api/settings.py:52`** — `parents[3]` apuntaba **fuera del repo**: el `.env` nunca se leía. Con un `.env` válido salían 6 errores `Field required`.
3. **`apps/api/settings.py:55`** — `extra="forbid"` contra un `.env` compartido por 5 consumidores → **13 `Extra inputs are not permitted`**.
4. **`apps/api/main.py:62-66`** — dos fuentes de verdad: `Settings` leía del `.env`, `main.py` leía de `os.environ`. Con el `.env` **correcto**, `DATABASE_URL` quedaba vacío → 500 en cada endpoint, 503 en cada subida, `KeyError` en el bucket.
5. **`notebooks/01`** — `import re` ausente y `re.fullmatch` usándose para validar la salida del LLM: el worker fallaba **después** de pagar la búsqueda y la inferencia.

Los restantes: `settings.py` ausente en la imagen Docker y `pydantic-settings` sin declarar; `docker-compose.dev.yml` con contexto de build, comando y volúmenes rotos; CI con ruta inexistente, `jupyter` no instalado y un bucle de espera que nunca fallaba.

---

## 4. La ruta de Mixpost (P0 + decisión D2)

El repositorio migró a Mixpost pero conservó el adaptador de Postiz, y **la API de Mixpost no es compatible con la de Postiz**. Contrato verificado contra el OpenAPI oficial:

| | Postiz (no sirve) | Mixpost (implementado) |
|---|---|---|
| Base | `{base}/integrations`, `/upload`, `/posts` | `{base}/{workspace}/accounts`, `/media`, `/posts` |
| Auth | `Authorization: <key>` | `Authorization: Bearer <clave personal>` |
| Cuentas | `identifier` + id string | `provider` + id **numérico** |
| Publicación | `posts:[{integration, value, settings}]` | `accounts:[ids]` + `versions:[{content:[{body, media:[ids]}], options:{provider:{…}}}]` |
| Medios | objeto `{id, path}` | **id numérico** |
| Opciones | `settings.__type` plano | indexadas por proveedor (`facebook_page`, `instagram_standalone`, `tiktok.privacy_level` por cuenta) |

Cambios: nuevo `adapters/mixpost_adapter.py`, eliminados `postiz_adapter.py` y su test, nuevo `tests/test_mixpost_adapter.py` (5 tests sobre el payload real), publisher actualizado, compose con `MIXPOST_CORE_PATH` y URL interna `http://mixpost/mixpost/api`, perfiles alineados (`publisher` arrastra `mixpost`).

> ⚠️ **D2 — Limitación de licencia.** La tabla de features oficial de <https://mixpost.app/pricing> lista **API, MCP y webhooks sólo en Pro (USD 299 único) y Cloud**. **Lite (MIT, gratis) no incluye la API REST** y sólo publica en Facebook Pages, X y Mastodon: Instagram, TikTok y YouTube requieren Pro. Con Lite, la vía soportada es la manual que ya describe el README.

**Dos acciones tuyas:** (1) crear la **clave personal** en la página API del panel → `MIXPOST_API_KEY` (`MIXPOST_APP_KEY` es la clave de Laravel, *no* sirve); (2) habilitar el **acceso público r2.dev** del bucket `marketing-content-media`, o Mixpost no podrá descargar el vídeo.

---

## 5. Configuración

**Para arrancar la API:** `DATABASE_URL`, `WORKER_API_TOKEN`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.
**Para los workers:** `API_BASE_URL` (HTTPS público), `SCRIPT_SPACE_URL`, `VOICE_STUDIO_URL`, `HF_TOKEN`, `YOUTUBE_API_KEY`.
**Para publicar:** `MIXPOST_BASE_URL`, `MIXPOST_API_KEY`, `MIXPOST_WORKSPACE_UUID`, `MIXPOST_TIMEZONE`, `STORAGE_PUBLIC_BASE_URL`.
**Dashboard (build time):** `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_MIXPOST_URL`.

Declaradas pero **no leídas** por el código: `REDIS_URL`, `YOUTUBE_CLIENT_ID/SECRET`, `TIKTOK_CLIENT_KEY`, `META_APP_ID/SECRET` (las usa la UI de Mixpost al autorizar cuentas).

---

## 6. Decisiones abiertas

- **D1 — Auth de la API.** 8 rutas públicas: crear jobs sin límite y subir 250 MB sin autenticar. Opciones: token de sesión + CORS cerrado · sólo lectura pública con escritura restringida · proxy inverso que autentique por delante.
- **D2 — Edición de Mixpost.** Pro (USD 299) · Cloud · Lite con publicación manual.
- **D4 — Cuenta de los Spaces.** El `.env` apunta a `javiertarazon-*` mientras `deploy_hf_spaces.sh` deriva el id del usuario autenticado.

---

## 7. Verificación ejecutada

```
pytest -q                                             → 31 passed, 1 warning
import main (con el .env real)                         → OK
import main (sin configuración)                        → ValidationError (fail-fast correcto)
docker compose -f docker-compose.dev.yml config        → OK
docker compose --env-file .env -f infra/... config     → OK (publisher, scheduler, colab-tunnel)
pyflakes apps packages scripts tests                   → 0 problemas
compile() de las celdas de ambos notebooks             → compilan ambas
git fsck                                               → 12 commits, 152 blobs, 94 trees, sin corrupción
```

**Entorno:** `.venv` del repositorio (Python 3.15.2) con `apps/api/requirements.txt` + `requirements-dev.txt`.

---

## 8. Lo que falta para el 100 %

1. **Commit y push** (necesita credenciales de GitHub en esta máquina).
2. **Clave personal de Mixpost** + edición con API (Pro o Cloud).
3. **Acceso público r2.dev** del bucket.
4. **Decidir D1** (modelo de auth) y **D4** (cuenta de los Spaces).
5. Pendientes de código priorizados en el JSON: pool de conexiones, adaptador de voz con contrato roto, validación de esquema en `href` del dashboard, `limit_req`/timeouts en el gateway nginx, uso real de los tipos generados.
