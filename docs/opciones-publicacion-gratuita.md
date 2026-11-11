# Opciones gratuitas y open source para publicar en TikTok, Instagram y YouTube

**Fecha:** 2026-11-05 · **Contexto:** decisión D2 del manifiesto de trazabilidad (qué scheduler usar en `publisher-service`).

---

## 1. La corrección de partida

«Postiz es de pago» es cierto **sólo del Postiz Cloud** ($29/mes el plan más barato). El **Postiz open source es AGPL-3.0 y se puede autoalojar gratis, sin recorte de features** respecto a su versión en la nube. Lo mismo ocurre con Mixpost: la edición **Lite (MIT) es gratis pero NO incluye la API REST**; la **API va en Pro (USD 299 único)**.

| | Cloud | Self-hosted |
|---|---|---|
| Postiz | $29/mes | **AGPL-3.0, gratis, sin límites de features** |
| Mixpost | suscripción | Lite MIT gratis (FB/X/Mastodon, **sin API**) · Pro $299 con API |

Además, las APIs oficiales de las tres redes son **gratuitas**: el coste nunca está en la licencia del scheduler, sino en registrar tu propia app de desarrollador en cada red.

---

## 2. Comparativa de las opciones gratuitas con TikTok + Instagram + YouTube

| Proyecto | Licencia | Coste | TikTok | Instagram | YouTube | API para tu código | Stack mínimo | madurez |
|---|---|---|---|---|---|---|---|---|
| **Postiz** | AGPL-3.0 | **gratis** | ✅ (`tiktok`, `tiktok-business`) | ✅ (`instagram`, `instagram-standalone`) | ✅ | REST pública (6 ops), MCP, CLI, webhooks | Next/NestJS + Postgres + Redis + **Temporal** + storage | ★★★★★ 35k+ ★, activo |
| **TryPost** | AGPL-3.0 | **gratis** | ✅ | ✅ | ✅ (Shorts) | REST + MCP (Laravel Passport) | **1 imagen Docker** + Postgres + Redis (Horizon+Reverb incluidos) | ★★★★☆ v2.0.0 oct-2026 |
| postiz-cheap | MIT | gratis | ✅ | parcial | parcial | por definir | Node + SQLite | ★★★☆☆ 1.4k ★ |
| Open-Dispatch | MIT | gratis | ✅ | ✅ | ✅ (Shorts) | una sola API | — | ★★☆☆☆ 20 ★ |
| postbase | MIT | gratis | ✅ | ❌ | ✅ | MCP + REST | — | ★★☆☆☆ 24 ★ |
| postflare | — | gratis (plan Cloudflare) | ✅ | ✅ | ✅ | CLI + MCP, 36 plataformas | Workers + D1 + R2 | ★★☆☆☆ |
| Socioboard 5 | GPL-3.0 | gratis | ❌ | ✅ | ✅ | internas | Node/PHP | ★★★☆☆ set antiguo |
| Activepieces | MIT | gratis | ❌ | ✅ | ✅ | Flows + piezas MCP | Docker | ★★★☆☆ sin pieza de TikTok |

**Descartados por no cubrir las tres redes:** Open-Post (X/LinkedIn/Reddit/Bluesky), crossposter (sin TikTok), Activepieces como scheduler único (sin TikTok).

### La opción más simple de todas: APIs directas, sin intermediario

Las tres APIs oficiales son gratuitas y se pueden usar desde tu propio `publisher-service`:

| Red | API | Coste | Requisitos |
|---|---|---|---|
| TikTok | Content Posting API | gratis | app registrada + revisión; publicación directa (`privacy_level`), y modo *draft* |
| Instagram | Graph API (Content Publishing) | gratis | cuenta Business o Creator **vinculada a una Página de Facebook**; Reels soportado |
| YouTube | Data API v3 | gratis | cuota de 10.000 unidades/día; subida de vídeo como *resumable upload* |

Coste: escribes tú la subida, la programación y la gestión de tokens. Beneficio: cero dependencias, cero licencias, control total.

---

## 3. El cuello de botella real: las aprobaciones

Ningún scheduler gratis te ahorra esto, y es donde se van las semanas:

| Red | Qué necesitas | Tiempo típico |
|---|---|---|
| YouTube | Google Cloud project + OAuth + API habilitada | **minutos** |
| Instagram | app de Meta, Business account, Página de FB, revisión de permisos (`instagram_content_publish`) | **días a semanas** |
| TikTok | app de TikTok for Developers + revisión de Content Posting API + app review | **días a semanas** |

En los tres casos las revisiones son unpredictables. **Empieza por TikTok e Instagram en paralelo**, porque son las que bloquean.

Postiz y TryPost te dan el OAuth ya resuelto, pero **self-hosted implica que tú registras y apruebas cada app** (las versiones en la nube usan apps preaprobadas). En los docs de Postiz: *"Meta and TikTok app reviews can take weeks"*.

---

## 4. Recomendación para este repositorio

**Postiz self-hosted**, por una razón concreta: **el adaptador que ya tenías (`postiz_adapter.py`) hablaba exactamente la API pública de Postiz**, y sigue recuperable desde el historial:

```bash
git show e0acb49:apps/publisher-service/adapters/postiz_adapter.py
```

Su contrato coincide con la API real de Postiz:

| Llamada | Endpoint Postiz |
|---|---|
| Listar canales | `GET {base}/integrations` |
| Subir medio | `POST {base}/upload` |
| Publicar | `POST {base}/posts` con `settings.__type` = `youtube` / `tiktok` / `instagram` / `instagram-standalone` |
| Auth | `Authorization: <api_key>` (API key simple) |

Y tu `.env` original ya traía `POSTIZ_BASE_URL=https://api.postiz.com/public/v1` y `POSTIZ_API_KEY=` — es decir, **el código era correcto para Postiz; el bug real era la deriva de nombres** (`POSTIZ_*` en el adaptador vs `MIXPOST_*` en el resto del repo).

### Reglas de medios de Postiz que el pipeline debe respetar

| Canal | Límite | Regla de medios |
|---|---|---|
| YouTube | 5.000 caracteres | **exactamente un adjunto, y debe ser un vídeo** |
| TikTok | 2.000 caracteres | vídeo o carrusel de fotos |
| Instagram | 2.200 caracteres | feed, carrusel, Reel o Story |

Tu pipeline produce **un único MP4**, así que encaja con la regla de YouTube sin cambios.

### Puesta en marcha (self-hosted)

```bash
# Postiz: Next/NestJS + Postgres + Redis + Temporal + almacenamiento
docker compose -f docker-compose.yml up -d
# Variables: MAIN_URL, FRONTEND_URL, NEXT_PUBLIC_BACKEND_URL, JWT_SECRET,
#            DATABASE_URL, REDIS_URL, TEMPORAL_ADDRESS, STORAGE_PROVIDER, UPLOAD_DIRECTORY
# Credenciales de red: FACEBOOK_APP_ID/SECRET, INSTAGRAM_APP_ID/SECRET,
#                      TIKTOK_CLIENT_ID/SECRET, GOOGLE_CLIENT_ID/SECRET (YouTube)
```

**TryPost** es más ligero de operar (una sola imagen, ~1 vCPU / 512 MB–1 GB RAM, sólo Postgres + Redis) y además habla **R2 nativo** (`FILESYSTEM_DISK=r2` + `R2_*`), lo que encaja con tu almacenamiento actual. Pero su API es un contrato distinto (Laravel Passport), así que reescribirías el adaptador.

---

## 5. Configuración que haría falta en cada caso

**Postiz / TryPost self-hosted:**

```
POSTIZ_BASE_URL=https://postiz.tudominio.com/public/v1     # o /api en TryPost
POSTIZ_API_KEY=<clave de la página API de Postiz>          # Postiz
# TryPost: Bearer <token de Passport>
```

**Redes (una app de desarrollador por red, en ambos casos):**

```
FACEBOOK_APP_ID / FACEBOOK_APP_SECRET        # Instagram (vía Página de FB)
INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET
TIKTOK_CLIENT_ID / TIKTOK_CLIENT_SECRET
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET      # YouTube
```

**Medios (ya lo tienes):** `STORAGE_PUBLIC_BASE_URL` debe apuntar al host público del mismo bucket R2, o el scheduler no podrá descargar el vídeo.

---

## 6. Resumen ejecutivo

| Pregunta | Respuesta |
|---|---|
| ¿Hay algo gratis que publique en TikTok + IG + YouTube? | **Sí: Postiz self-hosted (AGPL-3.0) es el más maduro; TryPost el más ligero de operar.** |
| ¿Postiz es de pago? | El Cloud sí. **El self-hosted es gratis y sin recorte de features.** |
| ¿Por qué descartamos Mixpost Lite? | La API REST va en Pro (USD 299) y Lite sólo publica en FB/X/Mastodon. |
| ¿Cuál es el coste real? | Tu servidor (~USD 5-15/mes) + **las revisiones de developer app de Meta y TikTok, que pueden tardar semanas**. |
| ¿Cuánto trabajo de código? | **~0** con Postiz: el adaptador original ya está hecho y sigue en el historial de git. |

---

## 7. Decisión final (5-nov-2026)

**Scheduler elegido: Open-Dispatch (MIT).** Es el único candidato con licencia MIT que publica en TikTok, Instagram y YouTube a la vez y expone API propia.

| Opción | Licencia | API | TikTok + IG + YouTube | Coste | Veredicto |
|---|---|---|---|---|---|
| **Open-Dispatch** | MIT | propia (`POST /dispatch`) | ✅ | 0 € | **Elegido** |
| Postiz self-hosted | AGPL-3.0 | propia | ✅ | 0 € (exige Temporal) | Descartado por licencia |
| TryPost | AGPL-3.0 | propia | parcial | 0 € | Descartado por licencia y alcance |
| Mixpost Lite | MIT | ❌ solo en Pro/Cloud | ❌ | 299 € (Pro) | Descartado |
| postiz-cheap | MIT | propia | ✅ | MuAPI de pago | Descartado |

**Estado de la implementación:** el `publisher-service` ya encola contra la API real de Open-Dispatch (verificado contra su código, commit `773dd54`), con 16 tests propios y un smoke test end-to-end por HTTP. Falta lo externo: las credenciales OAuth de cada red (`TIKTOK_ACCESS_TOKEN`, `IG_USER_ID`/`IG_TOKEN` y el trío de YouTube) y el acceso público `r2.dev` del bucket. Detalle operativo en el README y en `docs/estado-y-trazabilidad-marketing-content-os.json`.
