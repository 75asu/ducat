# ducat , one small image, runs either mode (serve | run).
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[push,aws,gcp]"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
COPY --from=build /install /usr/local
# Non-root by default.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin ducat
USER 10001
ENTRYPOINT ["ducat"]
CMD ["--help"]
