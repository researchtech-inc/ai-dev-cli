# template_id = "agent/Makefile.delegate"
# template_version = 1

SHELL := /usr/bin/env bash

.PHONY: format
format:
	@dev format

.PHONY: lint
lint:
	@dev lint

.PHONY: typecheck
typecheck:
	@dev typecheck

.PHONY: test
test:
	@dev test --lane=unit

.PHONY: check
check:
	@dev check

.PHONY: verify
verify:
	@dev verify

.PHONY: pre-commit
pre-commit:
	@dev check
