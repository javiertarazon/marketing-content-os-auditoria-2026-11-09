FROM python:3.12-slim

# Usuario sin privilegios: el AGENTS.md exige que nadie corra como root.
# El uid 1001 es distinto del 999 (dispatch) del volumen /media de
# Open-Dispatch, pero esta API no escribe en ese volumen.
RUN groupadd --system --gid 1001 api \
    && useradd --system --uid 1001 --gid 1001 --home-dir /app --shell /usr/sbin/nologin api

WORKDIR /app
# Se instala el lockfile (versiones exactas + hashes) para que la imagen sea
# reproducible. El uso y la regeneracion estan documentados en el propio
# apps/api/requirements.lock.txt.
COPY apps/api/requirements.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY packages/python/core /app/core
COPY apps/api/main.py .
COPY apps/api/settings.py .
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
USER 1001:1001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
