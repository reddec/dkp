NEXT_MINOR := $(shell git describe --tags | head -n 1 | python3 -u -c 'x = input().split("."); x[-2] = str(int(x[-2])+1); x[-1] = "0"; print(".".join(x))')
NEXT_PATCH := $(shell git describe --tags | head -n 1 | python3 -u -c 'x = input().split("."); x[-1] = str(int(x[-1])+1); print(".".join(x))')

test:
	 --noprofile --norc -eo pipefail

bump-patch:
	poetry version "$(NEXT_PATCH)"
	git add pyproject.toml
	git commit -m "release $(NEXT_PATCH)"
	git tag "$(NEXT_PATCH)"


bump-minor:
	poetry version "$(NEXT_MINOR)"
	git add pyproject.toml
	git commit -m "release $(NEXT_MINOR)"
	git tag "$(NEXT_MINOR)"

bump: bump-minor

build:
	poetry build

.PHONY: build test bump bump-minor