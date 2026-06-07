# agent-tools MCP server — Cloud Run container.
# Cloud Functions use buildpacks; this is a plain Cloud Run service, so it needs
# an explicit image with our uvicorn start command.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# Cloud Run injects PORT (default 8080). Shell form so ${PORT} expands at runtime.
ENV PORT=8080
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
