"""Kết nối Iceberg (REST catalog trên PostgreSQL, dữ liệu Parquet trên MinIO) bằng PyIceberg.

Cấu hình đọc từ .env ở thư mục gốc đồ án:
    ICEBERG_REST_URI   mặc định http://localhost:8181
    S3_ENDPOINT        mặc định http://localhost:9000
    MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, AWS_REGION
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
from dotenv import load_dotenv
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, BooleanExpression
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table

ROOT = Path(__file__).resolve().parents[2]   # thư mục gốc đồ án
load_dotenv(ROOT / ".env")

_catalog: Catalog | None = None


def get_catalog() -> Catalog:
    """Catalog dùng chung cho mọi script ingestion (tạo một lần mỗi tiến trình)."""
    global _catalog
    if _catalog is None:
        s3_endpoint = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
        _catalog = load_catalog("lakehouse", **{
            "type": "rest",
            "uri": os.environ.get("ICEBERG_REST_URI", "http://localhost:8181"),
            "s3.endpoint": s3_endpoint,
            "s3.access-key-id": os.environ["MINIO_ROOT_USER"],
            "s3.secret-access-key": os.environ["MINIO_ROOT_PASSWORD"],
            "s3.region": os.environ.get("AWS_REGION", "us-east-1"),
            "s3.path-style-access": "true",
        })
        # REST catalog có thể trả về endpoint nội bộ Docker (http://minio:9000) mà máy ngoài
        # không phân giải được: ép dùng endpoint của client.
        _catalog.properties["s3.endpoint"] = s3_endpoint
    return _catalog


def ensure_namespace(namespace: str) -> None:
    try:
        get_catalog().create_namespace(namespace)
        print(f"Đã tạo namespace {namespace}")
    except NamespaceAlreadyExistsError:
        pass


def ensure_table(identifier: str, schema: Schema, partition_spec: PartitionSpec) -> Table:
    """Tạo bảng nếu chưa có; bảng đã có thì giữ nguyên."""
    catalog = get_catalog()
    try:
        return catalog.load_table(identifier)
    except NoSuchTableError:
        table = catalog.create_table(identifier, schema=schema, partition_spec=partition_spec)
        print(f"Đã tạo bảng {identifier}")
        return table


def load_table(identifier: str) -> Table:
    return get_catalog().load_table(identifier)


def existing_keys(table: Table, cols: list[str],
                  row_filter: BooleanExpression = AlwaysTrue()) -> set[tuple]:
    """Tập các bộ giá trị (cols) đã có trong bảng, dùng để chống trùng trước khi ghi."""
    df = table.scan(row_filter=row_filter, selected_fields=tuple(cols)).to_arrow()
    return set(zip(*(df.column(c).to_pylist() for c in cols))) if df.num_rows else set()


def append(table: Table, rows: list[dict]) -> int:
    """Ghi thêm các dòng (dict theo tên cột) thành một snapshot. Trả về số dòng đã ghi."""
    if not rows:
        return 0
    arrow_schema = schema_to_pyarrow(table.schema())
    table.append(pa.Table.from_pylist(rows, schema=arrow_schema))
    return len(rows)


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
