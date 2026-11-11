FROM python:3.12-slim

# El publicador comparte el volumen de medios (/media) con Open-Dispatch, que
# corre como el usuario "dispatch" (uid 999). Si el publicador corriera como
# root, los ficheros que crea en /media/renders quedarian como root: el worker
# podria leerlos (modo 644) pero no borrarlos ni sustituirlos.
#
# Se ejecuta con el MISMO uid 999 y se precrea /media con esa propiedad, de
# modo que el volumen compartido tenga el mismo propietario tanto si lo crea
# el contenedor del publicador como si lo crea el de Open-Dispatch.
RUN groupadd -g 999 dispatch 2>/dev/null || true \
    && useradd -u 999 -g 999 -M -d /nonexistent -s /usr/sbin/nologin dispatch 2>/dev/null || true \
    && mkdir -p /media \
    && chown -R 999:999 /media

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY adapters /app/adapters

# El usuario no puede escribir en /app: se evita intentar generar .pyc.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 999:999

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
