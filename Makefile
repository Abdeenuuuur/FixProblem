.PHONY: install data seed run test evaluate screenshots video

install:
	pip install -r requirements.txt
data:
	python data/generate_sample_data.py
seed:
	python scripts/seed_demo.py
run: seed
	python -m fixproblem.app
test:
	python -m pytest -q || python -m unittest discover -s tests -t .
evaluate:
	python scripts/evaluate.py
screenshots:
	python scripts/take_screenshots.py
video:
	python scripts/record_tutorials.py
	python scripts/frame_screenshots.py
