#!/usr/bin/env bash
# Generate Python gRPC stubs from .proto files.
# Usage: ./proto/generate_python.sh
# Output: agent-core/proto_gen/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PROTO_DIR="$SCRIPT_DIR"
OUT_DIR="$REPO_ROOT/agent-core/proto_gen"

mkdir -p "$OUT_DIR"

python -m grpc_tools.protoc \
    --proto_path="$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    --pyi_out="$OUT_DIR" \
    "$PROTO_DIR"/course.proto \
    "$PROTO_DIR"/student.proto \
    "$PROTO_DIR"/assignment.proto

# Create __init__.py for the generated package
touch "$OUT_DIR/__init__.py"

# Fix imports: convert bare imports to relative imports within the package
# e.g., "import course_pb2" → "from . import course_pb2"
for f in "$OUT_DIR"/*_pb2_grpc.py "$OUT_DIR"/*_pb2.py; do
    [ -f "$f" ] || continue
    sed -i '' 's/^import \([a-z_]*_pb2\) as /from . import \1 as /g' "$f"
    sed -i '' 's/^from \([a-z_]*_pb2\) import /from .\1 import /g' "$f"
done

echo "Python gRPC stubs generated in $OUT_DIR"
