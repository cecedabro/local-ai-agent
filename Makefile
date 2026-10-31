SHELL := bash
SH_FILES := $(shell git ls-files '*.sh') pc/power/ai-power-remote
PY_FILES := $(shell git ls-files '*.py')
YAML_FILES := $(shell git ls-files '*.yaml' '*.yml')

.PHONY: lint test
lint:
	shellcheck $(SH_FILES)
	python3 -m py_compile $(PY_FILES)
	python3 -c 'import sys, yaml; [list(yaml.safe_load_all(open(f))) for f in sys.argv[1:]]' $(YAML_FILES)
	python3 -c 'import json, sys; [json.load(open(f)) for f in sys.argv[1:]]' devbox/opencode.json renovate.json

test:
	harness/tests/selftest.sh
