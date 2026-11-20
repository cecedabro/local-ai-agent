# Coder chat (chat/SPEC.md). Build from the repo root:
#   docker build -f chat/Dockerfile -t coder-chat:local .
FROM python:3.11.11-slim
WORKDIR /app
# DejaVu fonts: PDFs made by files.render_pdf can show every character (SPEC §16.4)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY chat/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY chat/coder ./coder
COPY chat/static ./static
RUN useradd --uid 10001 --no-create-home coder && mkdir /data && chown 10001 /data
USER 10001
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME /data
CMD ["uvicorn", "--factory", "coder.app:create_app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
