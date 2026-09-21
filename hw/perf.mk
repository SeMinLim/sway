# Isolated testbench builds and logs; never overwrite the stress-test evidence.
PERF_ROOT ?= $(PROJECT_DIR)/perf/$(SIM_BACKEND)
PERF_RESULTS ?= $(PROJECT_DIR)/results/perf/$(SIM_BACKEND)

.PHONY: perf perf-single perf-stream check-perf-parser

perf: perf-single perf-stream
	cd "$(PROJECT_DIR)" && $(PYTHON) reference/check_perf.py \
		--single-log "$(PERF_RESULTS)/single.log" --single-stderr "$(PERF_RESULTS)/single.stderr.log" \
		--stream-log "$(PERF_RESULTS)/stream.log" --stream-stderr "$(PERF_RESULTS)/stream.stderr.log" \
		--backend "$(SIM_BACKEND)" --output "$(PERF_RESULTS)/summary.json"

perf-single:
	mkdir -p "$(PERF_RESULTS)"
	rm -f "$(PERF_RESULTS)/summary.json" "$(PERF_RESULTS)/single.log" "$(PERF_RESULTS)/single.stderr.log"
	$(MAKE) -C "$(PROJECT_DIR)" bsim SIM_BACKEND=$(SIM_BACKEND) \
		BSIM_TOP_SOURCE="$(PROJECT_DIR)/sim/TbSwayPerf.bsv" BSIM_TOP_MODULE=mkTbSwayPerfSingle \
		BSIM_DIR="$(PERF_ROOT)/single"
	cd "$(PROJECT_DIR)" && "$(PERF_ROOT)/single/bsim" 2> "$(PERF_RESULTS)/single.stderr.log" | tee "$(PERF_RESULTS)/single.log"

perf-stream:
	mkdir -p "$(PERF_RESULTS)"
	rm -f "$(PERF_RESULTS)/summary.json" "$(PERF_RESULTS)/stream.log" "$(PERF_RESULTS)/stream.stderr.log"
	$(MAKE) -C "$(PROJECT_DIR)" bsim SIM_BACKEND=$(SIM_BACKEND) \
		BSIM_TOP_SOURCE="$(PROJECT_DIR)/sim/TbSwayPerf.bsv" BSIM_TOP_MODULE=mkTbSwayPerf \
		BSIM_DIR="$(PERF_ROOT)/stream"
	cd "$(PROJECT_DIR)" && "$(PERF_ROOT)/stream/bsim" 2> "$(PERF_RESULTS)/stream.stderr.log" | tee "$(PERF_RESULTS)/stream.log"

check-perf-parser:
	cd "$(PROJECT_DIR)/reference" && $(PYTHON) -m unittest -v test_check_perf.py
