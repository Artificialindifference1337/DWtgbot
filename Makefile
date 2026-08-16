.PHONY: install init run test check
install:
	python -m pip install -r requirements.txt
init:
	python -m app.init_db
run:
	python -m app.main
test:
	pytest -q
check:
	python -m compileall -q app tests
