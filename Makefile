.PHONY: install install-venv install-dev test web clean bundle

install:
	bash install.sh

install-venv:
	bash install.sh --venv

install-dev:
	bash install.sh --dev

test:
	pytest

web:
	trace web

clean:
	rm -rf .pytest_cache traceapp/__pycache__ tests/__pycache__ *.egg-info build dist

bundle: clean
	mkdir -p dist
	tar --exclude=.trace --exclude=.venv --exclude=dist -czf dist/trace-operator-src.tgz .
