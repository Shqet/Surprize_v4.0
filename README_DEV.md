# Dev Quickstart (Windows)

## 1) Create venv
py -3.11 -m venv .venv
.venv\Scripts\activate

## 2) Install
python -m pip install --upgrade pip
pip install -e .[dev]

## 3) Run
python -m app.main

## 4) Lint / format / typecheck
ruff check .
ruff format .
black .
mypy app
pytest -q
