FROM python:3.12-slim

WORKDIR /app

# Install system deps for grpcio
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .

# Generate proto stubs
RUN cd /app && python -m grpc_tools.protoc \
    --proto_path=../proto \
    --python_out=proto_gen \
    --grpc_python_out=proto_gen \
    --pyi_out=proto_gen \
    ../proto/*.proto 2>/dev/null || true

EXPOSE 8001

CMD ["python", "cli.py"]
