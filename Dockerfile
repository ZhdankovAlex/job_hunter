# job-hunter in a container: web UI + scripts. Code and data come from the host (volume in docker-compose.yml),
# the image has only the Python dependencies and Chromium for PDF CVs.
FROM python:3.11-slim

# Chromium — PDF CVs (cvgen), fonts: Liberation Sans has the same metrics as Arial from the CV template
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-liberation fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    JOBHUNTER_CHROME=/usr/bin/chromium \
    JOBHUNTER_HOST=0.0.0.0 \
    TZ=UTC

EXPOSE 8765
CMD ["python", "-m", "web", "--no-browser"]
