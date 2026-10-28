FROM python:3.12-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ARG TORCH_GROUP
RUN test -n "$TORCH_GROUP" || (echo "必須以 --build-arg TORCH_GROUP=cpu、cu130 或 rocm72 指定 PyTorch 版本" >&2 && exit 1)

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --group "$TORCH_GROUP"

COPY app ./app
COPY static ./static

RUN useradd --create-home --uid 10001 asr \
    && mkdir -p /data \
    && chown asr:asr /data
USER asr

CMD ["python", "-m", "app"]
