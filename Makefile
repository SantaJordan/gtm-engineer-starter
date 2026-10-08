# make check  : every test suite + the offline smoke test + the offline replay (no API keys)
# make replay : the whole method on Northwind through the real phase code, outputs kept
# make demo   : the smoke test, outputs kept
# make install-skills : link every skills/* folder into .claude/skills/ so Claude Code finds them
PY ?= python3

.PHONY: check test demo replay install-skills

check: test
	$(PY) examples/check.py
	$(PY) examples/northwind-demo/replay.py

replay:
	$(PY) examples/northwind-demo/replay.py --keep

test: export GTM_NO_DOTENV = 1
test:
	cd skills/crawford && $(PY) -m pytest -q -p no:cacheprovider tests
	cd skills/calibrated-judge && $(PY) -m pytest -q -p no:cacheprovider tests
	cd skills/win-loss-rewind && $(PY) -m pytest -q -p no:cacheprovider tests
	cd skills/dossier-builder && $(PY) -m pytest -q -p no:cacheprovider tests
	cd skills/dossier-data-auditor && $(PY) -m pytest -q -p no:cacheprovider tests

demo:
	$(PY) examples/check.py --keep

install-skills:
	mkdir -p .claude/skills
	for d in skills/*/; do n=$$(basename "$$d"); ln -sfn "../../skills/$$n" ".claude/skills/$$n"; done
	@ls .claude/skills/*/SKILL.md
