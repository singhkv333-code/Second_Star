.PHONY: dev test migrate migration shell clean install up down logs

install:
	pip install -r requirements.txt

up:
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f

dev:
	docker-compose up -d
	uvicorn backend.main:app --reload --port 8000

test:
	pytest tests/ -v --tb=short

migrate:
	alembic upgrade head

migration:
	alembic revision --autogenerate -m "$(name)"

shell:
	python -c "from backend.database import SessionLocal; db = SessionLocal(); print('DB session ready')"

clean:
	docker-compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
