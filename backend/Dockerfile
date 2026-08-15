FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Sécurité : ne pas tourner en root dans le conteneur. /app/data est créé ici et
# donné à l'utilisateur applicatif car c'est le point de montage du volume
# persistant (base SQLite) déclaré dans docker-compose.yml.
RUN mkdir -p /app/data && useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
