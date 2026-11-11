# AGENTS.md — Instrucciones globales para Marketing Content OS

Este fichero es la fuente de verdad de cómo se trabaja en este repositorio.
Applies a cualquier agente o persona que toque el código.

## 1. Idioma

- **Todo en español**: respuestas, mensajes de commit, documentación, docstrings,
  comentarios de código, nombres de variables de dominio y textos de UI.
- El código identificador técnico sigue las convenciones de Python/TypeScript
  (`snake_case`, `camelCase`), pero la documentación y los textos de cara al
  usuario siempre en español, con tildes y acentos correctos.

## 2. No alucinar: se verifica por ejecución

- **Una afirmación no es una prueba.** Antes de decir que algo funciona, se ha
  ejecutado. No vale decir "debería funcionar" ni "el código está bien".
- La evidencia válida es: `pytest` en verde, `pyflakes` sin avisos, un `curl`
  con el código HTTP real, un log de un contenedor, una captura de la salida.
- Si no se pudo verificar, se dice explícitamente: *no verificado*, con el
  motivo (falta una credencial, falta red, el daemon está apagado).
- No se reporta como hecho ninguna capacidad inferida de la lectura del código.
  Si la ruta está escrita pero nunca se ejecutó, se dice "implementado, sin
  ejecutar".

## 3. Trazabilidad y memoria del trabajo

- Cada cambio relevante queda registrado en
  `docs/estado-y-trazabilidad-marketing-content-os.json`, que es el manifiesto
  del proyecto: estado por módulo, defectos encontrados, decisiones y
  verificaciones.
- Los defectos encontrados **al ejecutar** (no al leer) se documentan con
  `severidad`, `file`, `problema`, `fix`, `status` y `verificacion`.
- El README y el manifiesto no deben contradecirse. Cuando se corrige una
  contradicción, se corrige en los dos sitios.
- Al retomar el trabajo, leer primero el manifiesto y `git log` para no
  repetir lo ya hecho.

## 4. Pruebas de todo lo que se codifique

- Todo cambio de comportamiento va acompañado de su prueba en `tests/`.
- Antes de cerrar una tarea se ejecuta la suite completa:
  `python -m pytest -q` (con el venv del repo) y se informa del número real
  de tests que pasan.
- Un cambio en la API exige probarla contra la base de datos real cuando
  toque esquema o SQL: no basta el test con dobles.
- Los cambios de UI se comprueban con `npm.cmd run build` (TypeScript en
  verde), no solo leyendo el JSX.
- Los workflows de CI se validan con `actionlint` antes de commitear.
- El compose se valida con `docker compose ... config` para cada perfil
  afectado.

## 5. Gestión del contexto

- Se trabaja por etapas verificables, no por reescrituras massive.
- Antes de tocar un fichero largo, se lee solo el tramo relevante.
- Los scripts auxiliares de análisis y parcheo viven fuera del repo, en
  `D:\proyectos_javier\_audit_recover\`, y no se versionan.
- Los subagentes reciben contexto autocontenido y devuelven un informe
  corto con evidencia `fichero:línea`, no volcados de código.
- Si un subagente falla o se corta, se revisa **su** trabajo antes de
  continuar: no se da por hecho que está bien.

## 6. Git

- Se commitea pronto: el entorno puede perder ficheros sin versionar.
- Los mensajes de commit van en un fichero (`git commit -F`) cuando llevan
  comillas, llaves o acentos: PowerShell los destroza si van inline.
- Formato: `<tipo>(<ámbito>): <qué cambia en una línea>`, cuerpo con el
  porqué, la evidencia y la referencia al run/issue cuando exista.
- Nunca se commitea `.env` ni nada con credenciales. `.env` está en
  `.gitignore`; los ejemplos (`.env.example`,
  `infra/.env.production.example`) solo llevan placeholders.
- Antes de publicar un repo, se audita el historial completo en busca de
  secretos.

## 7. Reglas del entorno (Windows / PowerShell 5.1)

- PowerShell 5.1: no existen `&&` ni `||`. Se separan sentencias y se
  comprueba `$LASTEXITCODE`.
- `npm` se invoca como `npm.cmd` (ExecutionPolicy bloquea `npm.ps1`).
- Para ejecutar en el repo, se usa `cwd` explícito en el proceso hijo
  (pytest necesita el directorio raíz como cwd).
- Al pasar rutas con punto y coma o con `$`, se construyen con `Join-Path`
  para evitar que PowerShell interprete la variable.
- **No se borran ficheros de forma permanente con comandos de shell.** Se usa
  `mavis-trash` o se mueven fuera del repo.
- Se preserva el estilo de finales de línea de cada fichero respecto a su
  blob anterior (el repo mezcla CRLF y LF y `core.autocrlf=false`). Una
  edición no debe convertir un fichero entero de estilo.
- No se imprimen secretos en respuestas, logs ni commits. Los tokens se leen
  del `.env` en tiempo de ejecución y solo se muestran como `<n chars>`.

## 8. Docker

- El daemon puede no estar levantado: se comprueba y se arranca Docker
  Desktop si hace falta.
- Los contenedores de Open-Dispatch corren como `uid 999 (dispatch)`. El
  publisher comparte el volumen de medios con ellos: **nadie debe correr como
  root** en un contenedor que escriba en `/media`, o el worker no podrá borrar
  lo que se cree.
- Las rutas Linux absolutas (`/content`, `/usr/bin/chromium`) no existen en
  Windows: el código portable las evita.

## 9. Flujo de trabajo de una tarea

1. Leer el manifiesto y el `git log` para conocer el estado.
2. Explorar y **medir** el estado real (no asumir).
3. Implementar el cambio mínimo que resuelve el problema.
4. Probar: test nuevo + suite completa + lint.
5. Documentar en el manifiesto y en el README si cambia un comportamiento
   observable.
6. Commit y push.
7. Si el objetivo global de la tarea queda cumplido, actualizar el
   manifiesto y cerrar con un informe de qué quedó verificado y qué quedó
   pendiente por acciones del usuario.
