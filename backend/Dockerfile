# The Misadventures of Havoc and Chaos — game engine image (Cloudflare Containers / any container host).
# The web client is served by the edge Worker, so this image is the Python engine only.
# Cloudflare Containers run linux/amd64 images (wrangler builds for that platform).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HAVOC_TTS_CACHE_DIR=/tmp/havoc-audio
WORKDIR /app
COPY requirements.txt ./
# Behind a TLS-inspecting proxy, pass its CA for this step only:
#   docker build --secret id=pip_ca,src=/path/to/ca.pem .   (never copied into the image)
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY app/ app/
RUN useradd --create-home havoc
USER havoc
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
