.PHONY: crm expense serve test lint smoke demo-core demo-full
crm:
	uv run uvicorn apps.crm.app:create_app --factory --host 127.0.0.1 --port 8101
expense:
	@echo 'Phase 5 pending its entry gate'; exit 1
serve:
	@echo 'Phase 1 pending Phase 0 live gate'; exit 1
test:
	uv run pytest -q
lint:
	uv run ruff check .
smoke:
	uv run python scripts/hello.py
demo-core:
	@echo 'Core demo unavailable until Phase 4 C1-C8 pass'; exit 1
demo-full:
	@echo 'Full demo unavailable until Phase 7 C1-C8 and E1-E6 pass'; exit 1
