"""Tạo namespace bronze và các bảng bronze. Chạy lại bao nhiêu lần cũng được.

Chạy:  python ingestion/common/init_bronze.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.common.bronze_schemas import ALL_TABLES, NAMESPACE  # noqa: E402
from ingestion.common.lakehouse import ensure_namespace, ensure_table  # noqa: E402


def main():
    ensure_namespace(NAMESPACE)
    for identifier, schema, spec in ALL_TABLES:
        table = ensure_table(identifier, schema, spec)
        print(f"{identifier}: {table.location()}")


if __name__ == "__main__":
    main()
