.PHONY: install migrate seed run test lint format

install:
	pip install -r requirements.txt
	pip install pytest pytest-django black ruff pre-commit

migrate:
	python manage.py makemigrations
	python manage.py migrate

seed:
	python manage.py seed

run:
	python manage.py runserver

test:
	python manage.py test

lint:
	ruff check .

format:
	black .
	pre-commit run --all-files || true
