test:
	python -m unittest discover -t . -s tests -v

sim:
	./tools/sim.sh

.PHONY: test sim
