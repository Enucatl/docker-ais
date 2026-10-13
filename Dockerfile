FROM node:22-alpine AS frontend-builder

ARG UPSTREAM_REVISION=6fc5464871ccb891f0e149b62df19d4ba6c33c9b
RUN apk add --no-cache git \
    && git clone https://github.com/dbeckerAC/ais-vessel-tracker.git /src \
    && git -C /src checkout --detach "$UPSTREAM_REVISION"

WORKDIR /src/frontend
RUN npm ci \
    && npm run build \
    && cp node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs dist/assets/maplibre-gl-worker.mjs \
    && cp node_modules/maplibre-gl/dist/maplibre-gl-shared.mjs dist/assets/maplibre-gl-shared.mjs

RUN cp /src/backend/app/config.py /src/backend/app/_upstream_config.py
COPY src /src
COPY vesselapi.patch /tmp/vesselapi.patch
RUN git -C /src apply /tmp/vesselapi.patch

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=frontend-builder /src/backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY --from=frontend-builder /src/backend/app ./app
COPY --from=frontend-builder /src/config ./config
COPY --from=frontend-builder /src/frontend/dist ./static
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
