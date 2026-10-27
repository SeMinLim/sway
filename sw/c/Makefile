PYTHON ?= python3
CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Werror

.PHONY: all generate check-generated test gap8 clean
all: build/validate

build/validate: sway.c sway.h sway_math.h generated/sway_model.c generated/sway_model.h tests/validate.c
	mkdir -p build
	$(CC) $(CFLAGS) -DSWAY_TRACE -fopenmp sway.c generated/sway_model.c tests/validate.c -o $@

generate:
	$(PYTHON) tools/export_model.py

check-generated:
	$(PYTHON) tools/export_model.py --check

test: check-generated
	$(PYTHON) tools/validate.py

gap8:
	$(MAKE) -f Makefile.gap8 clean all run platform=gvsoc PMSIS_OS=pulpos

clean:
	rm -rf build BUILD
