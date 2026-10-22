# syntax=docker/dockerfile:1
# CorpusStudy: the chat page and API. See docs/RUNBOOK.md (docker compose up).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# The package is installed in place (editable): its paths are resolved from /app.
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app src ./src
# An optional extra CA certificate for networks behind a TLS-inspecting proxy:
#   docker compose build  (no secret needed)  or  docker build --secret id=extra_ca,src=ca.crt .
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -e .

COPY --chown=app:app knowledge ./knowledge
COPY --chown=app:app eval ./eval
COPY --chown=app:app docs ./docs
RUN mkdir -p data indexes imports && chown app:app data indexes imports

USER app
ENV CORPUS_API_HOST=0.0.0.0 \
    CORPUS_API_PORT=8000
EXPOSE 8000
# The first start downloads the Bibles and builds the index (a few minutes). The check
# bypasses any HTTP(S)_PROXY settings: it talks to the container itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10m --retries=3 \
    CMD python -c "import urllib.request as u, sys; o = u.build_opener(u.ProxyHandler({})); sys.exit(o.open('http://127.0.0.1:8000/health', timeout=4).status != 200)"
CMD ["python", "-m", "corpusstudy.api", "--prepare"]
