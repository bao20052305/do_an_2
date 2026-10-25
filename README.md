# Đồ án 2: Giám sát và cảnh báo thời tiết nguy hiểm cho tàu thuyền trên Biển Đông

## Cấu trúc thư mục

```
.
├── .env                 # khóa API thật, không commit
├── .env.example         # mẫu .env
├── requirements.txt
├── docker-compose.yaml  # toàn bộ hạ tầng
├── docs/                # tổng kết, sơ đồ kiến trúc, lược đồ gold
├── ingestion/           # thu thập dữ liệu, ghi bronze
│   ├── ais/             # producer aisstream -> Redpanda
│   ├── gdacs/           # polling GDACS
│   ├── gfw/             # 4Wings presence, Vessels API
│   └── nchmf/           # crawl bản tin nchmf
├── streaming/flink/     # job Flink ghi bronze và cảnh báo mức 2
├── batch/
│   ├── spark/           # bronze -> silver -> gold, cảnh báo mức 1 (conf/spark-defaults.conf)
│   └── airflow/dags/    # lập lịch
├── serving/             # dashboard, API, trino/catalog/iceberg.properties
├── exploration/         # script thử nghiệm các nguồn (không chạy trong pipeline)
└── data/                # dữ liệu cục bộ, không commit
    ├── bronze/nchmf/    # đầu ra crawler nchmf
    └── exploration/     # đầu ra script thử nghiệm: ais, gdacs, gfw
```

## Chạy thử

```
pip install -r requirements.txt
copy .env.example .env     # rồi điền AISSTREAM_API_KEY, GFW_API_ACCESS_TOKEN
python ingestion/common/init_bronze.py          # tạo namespace bronze và bảng (cần docker compose đang chạy)
python ingestion/gdacs/fetch_gdacs.py           # GDACS -> bronze.gdacs_raw
python ingestion/nchmf/load_jsonl.py            # nạp JSONL cũ một lần
python ingestion/nchmf/crawl_nchmf_news.py      # NCHMF -> bronze.nchmf_bulletin
```

Các script tự tìm thư mục gốc đồ án, nên chạy từ thư mục nào cũng được.

## Hạ tầng

```
copy .env.example .env     # điền mật khẩu, AIRFLOW_FERNET_KEY, AIRFLOW_SECRET_KEY
docker compose up -d
```

| Dịch vụ | Địa chỉ |
| --- | --- |
| Redpanda Console | http://localhost:8080 |
| Redpanda (Kafka) | localhost:19092 |
| Flink UI | http://localhost:8081 |
| Spark master UI | http://localhost:8082 |
| Trino | http://localhost:8083 |
| Airflow | http://localhost:8085 |
| MinIO console | http://localhost:9001 |
| Iceberg REST catalog | http://localhost:8181 |
