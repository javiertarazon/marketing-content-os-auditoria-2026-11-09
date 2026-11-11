# Auditoría y estado operativo de Marketing Content OS

Fecha de verificación del código y entorno local: 2026-10-16.

## Resumen ejecutivo

**Nivel actual: MVP técnico, todavía no validado end-to-end.** El servicio de voz sintetizó correctamente una frase breve en una prueba directa. Se añadió búsqueda pública de páginas por tema usando ScrapeGraphAI y DuckDuckGo, sin claves de las redes; el flujo completo de Colab y la clonación con audio propio siguen sin validar. La búsqueda es de contenido indexado y accesible anónimamente, no un sistema de escucha social en tiempo real ni una métrica de tendencias.

En esta revisión se comprobaron:

- `21` pruebas Python aprobadas (con el entorno virtual local y dependencias de desarrollo).
- Build de producción del dashboard y comprobación TypeScript aprobados.
- `docker compose config --quiet` aprobado: sintaxis/configuración de Compose válida.
- `py_compile` aprobado para API, esquemas compartidos y app del Space de voz.
- Los notebooks se validan como JSON y sus celdas Python se analizan con AST en la suite.
- `.env` y `.env.cloudflare.local` no están rastreados por Git; se amplió `.gitignore` para cubrir `.env.*` y conservar solo las plantillas de ejemplo.

En la verificación del 2026-10-16 se levantaron API, dashboard, PostgreSQL y Redis; healthz y dashboard devolvieron HTTP 200, y una operación `HeadBucket` confirmó acceso al bucket R2. El Space privado de voz quedó `RUNNING`; se probó una síntesis corta y se descargó un WAV válido. No se ejecutó ningún worker de Colab ni se probó la clonación con una muestra WAV propia.

## Estado por módulo y plataforma

| Componente | Estado del código/configuración | Verificación en vivo |
|---|---|---|
| API FastAPI y PostgreSQL | Cola, lease, heartbeat, fallo/reintento, migración de progreso y biblioteca de medios implementados | API HTTP 200 y servicios activos en Docker |
| Dashboard Next.js | Seguimiento de etapa/porcentaje/errores, resultados de investigación, voz/audio, video y selector/importador de carpeta implementados | Dashboard HTTP 200; importación de archivo aún no probada en vivo |
| Biblioteca de medios/R2 | Importación WAV/MP4/JPG/PNG/WEBP, metadatos DB, objetos privados y enlaces firmados implementados | `HeadBucket` real aprobado; falta probar subida, descarga y firma |
| Worker investigación/voz | Busca páginas públicas por tema en las redes elegidas con ScrapeGraphAI/DuckDuckGo, acepta URL opcional, genera notas/guion y voz opcional por muestra/transcripción | Búsqueda de prueba devolvió resultados para las seis redes; tres páginas y tres fragmentos. Worker Colab y clonación con muestra pendientes |
| Búsqueda social pública | Consulta resultados web indexados por plataforma, extrae páginas anónimamente accesibles y, si no puede, usa fragmentos etiquetados del índice; no usa APIs de redes | En la prueba, Reddit/X/TikTok dieron fragmentos; YouTube/Instagram/Facebook devolvieron páginas públicas (incluidos perfil/página de negocio); no equivale a datos completos/recientes ni a métricas de tendencias |
| Hugging Face Voice Space | Space privado ZeroGPU que recibe texto y, opcionalmente, muestra y transcripción | `RUNNING`; síntesis de "Hola." produjo WAV válido, 24 kHz y 1,28 s |
| Worker render | Descarga audio/visual privado, usa fondo MP4/imagen, render Remotion y devuelve clave de video | Notebook validado estáticamente; no ejecutado en Colab |
| Hugging Face Script Space | Space remoto privado para guiones Qwen | Runtime `RUNNING`; no se ejecutó generación de guion en vivo |
| Cloudflare Quick Tunnel | Gateway limita rutas de worker e incluye progreso | Verificado: sin token devuelve 401 y rutas no permitidas 404; detenido al concluir |
| Mixpost Lite | Servicio opcional tras perfil `scheduler`; publicación manual | No se inició ni se probaron OAuth/redes sociales |
| VPS/GPU | No hay despliegue de producción documentado como realizado | Pendiente |

El entorno WSL disponible reportó Python 3.14.4. El proyecto documenta Python 3.11+ para pruebas y el contenedor de API usa Python 3.12; se validó la suite en 3.14.4, no en 3.11 ni dentro del contenedor.

## Cambios implementados en esta revisión

### Progreso y resultados por módulo

- `jobs.progress` almacena `stage`, `message` y `percent`; la inicialización migra trabajos existentes.
- API `POST /v1/jobs/{id}/progress` exige token de worker y lease válido.
- Quick Tunnel incluye la ruta autenticada de progreso, sin exponer la lista de trabajos ni la biblioteca.
- Los dos notebooks informan las etapas de investigación, voz, descarga/edición, render y almacenamiento.
- Dashboard consulta cada 10 segundos, muestra módulo, etapa, porcentaje y errores, y permite abrir investigación/guion, audio y MP4.

### Carpetas y recursos propios

- Se puede seleccionar una carpeta en el dashboard; los formatos soportados se importan uno a uno a R2.
- La API valida extensión, cabecera del formato y límite de 250 MB por archivo, y registra metadatos en PostgreSQL.
- Los tipos admitidos son WAV (muestra de voz), MP4, JPG, PNG y WEBP.
- La muestra WAV puede usarse para clonación zero-shot de OmniVoice con su transcripción exacta y confirmación de titularidad/autorización.
- Se puede seleccionar un MP4 o imagen para usarlo como fondo del render. El audio original del fondo de video se silencia.
- Los objetos usan bucket/keys privados; la API crea enlaces firmados de cinco minutos para reproducción/descarga local. Colab no accede a rutas del PC.
- El endpoint de subida, lista y enlaces de medios no forma parte de la whitelist del túnel.

## Bloqueos y soluciones pendientes

### 1. Búsqueda social pública de mejor esfuerzo

**Implementación:** el notebook instala el fork ScrapeGraphAI del proyecto fijado al commit `c75c8084fae2d4f5ba01a8c218bc1168b67e3569` y usa `scrapegraphai.utils.research_web.search_on_web` con DuckDuckGo para consultar `site:` por plataforma. No requiere claves de API de las redes. La URL de referencia pasó a ser opcional; la interfaz permite seleccionar YouTube, Reddit, X, Instagram, TikTok y Facebook. Se descargan páginas públicas, se filtran URLs para que pertenezcan al dominio de la red consultada y el dashboard muestra fuentes y estado por plataforma.

**Validación observada:** ScrapeGraphAI ejecutó sin claves consultas DuckDuckGo para YouTube, Reddit, X, Instagram, TikTok y Facebook, con el tema de prueba "marketing trends". Se obtuvieron resultados en las seis: páginas públicas para YouTube, Instagram y Facebook; fragmentos indexados para Reddit, X y TikTok. En una URL de Reddit el HTML respondió HTTP 200, pero solo contenía el texto "Reddit" (6 caracteres); el flujo no inventó el contenido del post y recurrió al fragmento indexado, marcado `snippet_only`.

**Limitaciones:** DuckDuckGo solo devuelve lo que indexa y puede limitar consultas; páginas con contenido cargado por JavaScript, privadas o que exigen inicio de sesión pueden no ser legibles. Los resultados pueden ser perfiles o páginas editoriales de una plataforma, no publicaciones sociales. Los fragmentos de búsqueda son parciales y se etiquetan para no presentarlos como el post completo. La implementación no sortea login, CAPTCHA ni controles de acceso y no proporciona métricas de volumen, ranking ni tendencias en tiempo real. No se han validado todavía el worker desde Colab ni el ciclo completo; el dashboard debe mostrar bloqueos o ausencia de resultados sin simular cobertura.

**Validación pendiente:** probar páginas públicas accesibles en las demás redes; después probar el ciclo dashboard → API → Colab → Qwen/voz → R2.

### 2. Síntesis de voz rechazada por cuota ZeroGPU

**Evidencia:** una solicitud solicitaba 270 segundos cuando quedaban 262, por lo que Hugging Face la rechazó antes de inferir. Se redujo `@spaces.GPU` de 180 a 120 segundos y se desplegó el Space actualizado. Luego una frase sintética produjo un WAV válido de 24 kHz y 1,28 segundos.

**Pendiente:** probar clonación con una muestra autorizada y su transcripción exacta; una llamada de texto no valida esa rama. La reserva máxima efectiva varía con la cuota/hardware y puede volver a rechazarse si la cuota restante es baja.

### 3. Colab depende del túnel y de Workers en ejecución

**Evidencia:** el notebook requiere `API_BASE_URL` HTTPS, `WORKER_API_TOKEN`, acceso S3 y, para research, HF token/URLs de Spaces. El archivo del notebook está cargado en Drive, pero no se ejecutó una tarea real desde Colab.

**Solución:** tras levantar Docker, iniciar `worker-gateway` y `cloudflared`, copiar la URL vigente del Quick Tunnel a Colab Secrets, ejecutar cada notebook y procesar un trabajo de prueba. El nombre del Quick Tunnel cambia al reiniciarlo; Colab puede suspender workers gratuitos.

### 4. R2 privado requiere validar operaciones de objetos

**Evidencia:** credenciales actuales permiten `HeadBucket`, pero no se probó una subida/descarga real ni la URL firmada desde dashboard.

**Solución:** importar un medio de prueba desde el dashboard y comprobar reproducción con URL firmada; mantener el bucket privado.

### 5. Mixpost no automatiza publicación

**Evidencia:** es opcional, usa perfil `scheduler`, requiere inicialización propia y no hay integración de publicación API desde el dashboard.

**Solución:** para publicar manualmente, arrancar Mixpost, completar su instalación y subir el MP4. OAuth real requiere dominio HTTPS estable y credenciales aprobadas por cada plataforma; no se ha validado.

## Requisitos de entorno y credenciales

### API local y dashboard

Usar el `.env` local (ignorado por Git), basado en `.env.example`, con:

- PostgreSQL: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`.
- Worker: `WORKER_API_TOKEN`.
- Hugging Face: `VOICE_STUDIO_URL`, `SCRIPT_SPACE_URL`, `HF_TOKEN`.
- Medios privados: `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.
- Mixpost opcional: variables `MIXPOST_*`.

`REDIS_URL` se configura, pero la cola MVP persiste/reserva en PostgreSQL; Redis no implementa la cola actual.

### Colab

Ambos notebooks requieren `API_BASE_URL`, `WORKER_API_TOKEN`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. Research requiere además `SCRIPT_SPACE_URL`, `VOICE_STUDIO_URL`, `HF_TOKEN`. El token HF debe leer Spaces privados; el token de worker no se debe reutilizar como credencial S3 ni Cloudflare.

### Cloudflare

El token Cloudflare REST API es de administración y no es necesario en runtime. Las credenciales S3 de R2 son distintas y deben limitarse al bucket. `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` y `R2_BUCKET_NAME` solo aplican a administración.

## Seguridad

- Los archivos `.env` no se deben subir a Git. La regla `.env.*` mantiene excepciones solo para las plantillas `.env.example`, `.env.colab.example` e `infra/.env.production.example`.
- Mantener el bucket privado; no poner tokens, audio, videos o datos personales en URLs públicas.
- Si cualquiera de los tokens/secretos compartidos anteriormente en la conversación sigue activo, revocarlo y crear otro antes de continuar; actualizar `.env` y Colab Secrets sin pegar las claves en el chat.
- Las muestras de voz se deben limitar a voces propias o para las que exista autorización. OmniVoice documenta clonación zero-shot por muestra y transcripción; revisar también sus licencias y condiciones de uso antes de uso comercial.
- Los notebooks y Spaces contienen plantillas; no significan que haya trabajos corriendo o recursos desplegados.

## Pasos para completar la prueba end-to-end

1. Iniciar Docker Desktop y habilitar integración WSL para Ubuntu.
2. Validar Compose, levantar API/dashboard/PostgreSQL y comprobar healthz/dashboard.
3. Confirmar que R2 está privado y que `.env` contiene credenciales S3 vigentes.
4. Importar una imagen/MP4 y un WAV de voz autorizado desde la carpeta local.
5. El Space de voz actualizado ya está desplegado y pasó una síntesis breve; falta probar clonación con WAV/transcripción. El Space de guiones está `RUNNING`, pero su inferencia no se ha probado.
6. Iniciar Quick Tunnel; actualizar `API_BASE_URL` en Secrets de ambos notebooks.
7. Ejecutar research y render en runtimes separados de Colab.
8. Crear una tarea desde el dashboard; comprobar etapas, investigación, guion, audio, render, enlace firmado y errores.
9. Repetir tras reiniciar la API para comprobar persistencia en PostgreSQL y R2.
10. Solo después de una ejecución real, evaluar VPS/GPU y dominio fijo.
