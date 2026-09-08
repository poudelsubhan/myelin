.PHONY: crm expense serve test lint smoke demo-core demo-full
crm:
	uv run uvicorn apps.crm.app:create_app --factory --host 127.0.0.1 --port 8101 --no-access-log
expense:
	uv run uvicorn apps.expense.app:create_app --factory --host 127.0.0.1 --port 8102
serve:
	uv run uvicorn myelin.console.app:create_app --factory --host 127.0.0.1 --port 8100
test:
	uv run pytest -q
lint:
	uv run ruff check .
smoke:
	uv run python scripts/hello.py
demo-core:
	uv run python scripts/demo.py --assert --core
demo-full:
	uv run python scripts/demo.py --assert --full
