FROM python:3.15-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    # Repositories are bind-mounted and usually owned by another uid.
    && git config --system --add safe.directory '*'

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

VOLUME ["/config"]
EXPOSE 8099
ENTRYPOINT ["git-repo-monitor", "-c", "/config/config.yaml"]
