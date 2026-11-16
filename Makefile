SHELL := bash
SH_FILES := $(shell git ls-files '*.sh') pc/power/ai-power-remote harness/tests/fakebin/gh
PY_FILES := $(shell git ls-files '*.py')
YAML_FILES := $(shell git ls-files '*.yaml' '*.yml')

.PHONY: lint test check status-test
lint:
	shellcheck $(SH_FILES)
	python3 -m py_compile $(PY_FILES)
	python3 -c 'import sys, yaml; [list(yaml.safe_load_all(open(f))) for f in sys.argv[1:]]' $(YAML_FILES)
	python3 -c 'import json, sys; [json.load(open(f)) for f in sys.argv[1:]]' devbox/opencode.json renovate.json

test:
	harness/tests/selftest.sh

# The agent loop's project check (CHECK_CMD=make check): fast, and no packages the devbox lacks.
# The contract tests (status/tests/test_contract.py) are each task's accept command instead,
# because they are built up group by group.
check:
	python3 -m py_compile $(PY_FILES)
	python3 -m unittest discover -q -s status/tests -p 'test_unit_*.py' || [ $$? -eq 5 ]

status-test:
	python3 -m unittest -q status/tests/test_contract.py status/tests/test_accept_history.py
