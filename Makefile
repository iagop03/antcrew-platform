# antcrew-platform development helpers
# Requires: pip install alembic aiosqlite

DB ?= platform.db
DATABASE_URL ?= sqlite+aiosqlite:///$(DB)

.PHONY: migrate migration check-migrations downgrade run test

## Run all pending Alembic migrations
migrate:
	DATABASE_URL=$(DATABASE_URL) alembic upgrade head

## Generate a new migration (NAME= required)
## Usage: make migration NAME=add_my_column
migration:
ifndef NAME
	$(error NAME is required — e.g. make migration NAME=add_my_column)
endif
	DATABASE_URL=$(DATABASE_URL) alembic revision --autogenerate -m "$(NAME)"
	@echo "Review the generated migration in alembic/versions/ before committing."

## Check that all migrations have been applied (non-zero exit if pending)
check-migrations:
	@python scripts/check_migrations.py

## Roll back one migration
downgrade:
	DATABASE_URL=$(DATABASE_URL) alembic downgrade -1

## Show current migration revision
current:
	DATABASE_URL=$(DATABASE_URL) alembic current

## Show migration history
history:
	DATABASE_URL=$(DATABASE_URL) alembic history --verbose

## Start the platform (dev mode: SQLite, no auth, auto-reload)
run:
	DATABASE_URL=$(DATABASE_URL) uvicorn app.main:app --reload --port 8000

## Run all tests
test:
	pytest tests/ -q

## Run tests with coverage
coverage:
	pytest tests/ -q --cov=app --cov-report=term-missing
