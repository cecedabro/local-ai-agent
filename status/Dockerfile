# agent-status service: one stdlib-only Python file plus its static files. Build from the repo root:
#   docker build -f status/Dockerfile -t agent-status:local .
FROM python:3.11.11-slim
WORKDIR /app
COPY status/app.py ./app.py
COPY status/static ./static
RUN useradd --uid 10001 --no-create-home status && mkdir /data && chown 10001 /data
USER 10001
ENV STATUS_PORT=8080 STATUS_DB=/data/status.db PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME /data
CMD ["python3", "/app/app.py"]
