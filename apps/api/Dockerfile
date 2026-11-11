FROM python:3.12-slim

WORKDIR /app
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/python/core /app/core
COPY apps/api/main.py .
COPY apps/api/settings.py .
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
