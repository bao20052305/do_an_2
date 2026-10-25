"""Schema và phân vùng của các bảng bronze. Mọi cột để optional để ghi được từ dữ liệu thô."""
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform, MonthTransform
from pyiceberg.types import (IntegerType, ListType, LongType, NestedField, StringType,
                             TimestampType, TimestamptzType)

NAMESPACE = "bronze"

# ---------------------------------------------------------------- GDACS
# Mỗi dòng là một lần gọi API, lưu nguyên văn JSON trả về.
GDACS_RAW = f"{NAMESPACE}.gdacs_raw"
GDACS_RAW_SCHEMA = Schema(
    NestedField(1, "endpoint", StringType()),          # event_list | event_detail | timeline | geometry
    NestedField(2, "eventid", LongType()),             # null với event_list
    NestedField(3, "episodeid", IntegerType()),        # null với event_list
    NestedField(4, "forecast_source", StringType()),   # nguồn dự báo của timeline, ví dụ JTWC
    NestedField(5, "request_url", StringType()),
    NestedField(6, "http_status", IntegerType()),
    NestedField(7, "payload", StringType()),
    NestedField(8, "payload_sha256", StringType()),
    NestedField(9, "fetched_at", TimestamptzType()),
    NestedField(10, "run_id", StringType()),
    NestedField(11, "ingest_version", StringType()),
)
GDACS_RAW_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="endpoint"),
    PartitionField(source_id=9, field_id=1001, transform=MonthTransform(), name="fetched_month"),
)

# ---------------------------------------------------------------- NCHMF
# Mỗi dòng là một bài trên nchmf.gov.vn.
NCHMF_BULLETIN = f"{NAMESPACE}.nchmf_bulletin"
NCHMF_BULLETIN_SCHEMA = Schema(
    NestedField(1, "post_id", LongType()),             # lấy từ URL (-post55211.html), khóa chống trùng
    NestedField(2, "source", StringType()),
    NestedField(3, "category", StringType()),
    NestedField(4, "url", StringType()),
    NestedField(5, "title", StringType()),
    NestedField(6, "listed_at_local", TimestampType()),   # giờ Việt Nam ghi trên trang, không múi giờ
    NestedField(7, "issued_at_text", StringType()),
    NestedField(8, "issued_at_utc", TimestamptzType()),
    NestedField(9, "content_text", StringType()),
    NestedField(10, "content_html", StringType()),
    NestedField(11, "content_sha256", StringType()),
    NestedField(12, "attachment_urls", ListType(element_id=100, element_type=StringType(),
                                                element_required=False)),
    NestedField(13, "http_status", IntegerType()),
    NestedField(14, "crawl_at", TimestamptzType()),
    NestedField(15, "crawler_version", StringType()),
    NestedField(16, "parse_warning", StringType()),
    NestedField(17, "run_id", StringType()),
)
NCHMF_BULLETIN_PARTITION = PartitionSpec(
    PartitionField(source_id=14, field_id=1000, transform=MonthTransform(), name="crawl_month"),
)

ALL_TABLES = [
    (GDACS_RAW, GDACS_RAW_SCHEMA, GDACS_RAW_PARTITION),
    (NCHMF_BULLETIN, NCHMF_BULLETIN_SCHEMA, NCHMF_BULLETIN_PARTITION),
]
