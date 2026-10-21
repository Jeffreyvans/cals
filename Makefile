.PHONY: install migrate seed run test lint format check precommit

install:
	pip install -r requirements.txt
	pip install pytest pytest-django factory-boy coverage ruff black pre-commit

migrate:
	python manage.py makemigrations
	python manage.py migrate

seed:
	python manage.py seed

run:
	python manage.py runserver

test:
	python manage.py test

pytest:
	pytest

lint:
	ruff check .
	black --check .

format:
	black .
	ruff check . --fix

check:
	python manage.py check
	python manage.py makemigrations --check --dry-run

precommit:
	pre-commit run --all-files
