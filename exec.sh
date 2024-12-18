export DB_HOST=127.0.0.1
export DB_NAME=DashboardGestaoAPI
export DB_USER=postgres
export DB_PORT=5433
export DB_PASSWORD=postgres
export KAFKA_BOOTSTRAP_SERVER=127.0.0.1:9092
export KAFKA_GROUP_ID=execution
export NR_SECONDS_NEXT_ATTEMPT=10

python3 app.py
