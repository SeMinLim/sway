# Isolated testbench builds and logs; never overwrite the stress-test evidence.
PERF_ROOT ?= $(PROJECT_DIR)/perf/$(SIM_BACKEND)
PERF_RESULTS ?= $(PROJECT_DIR)/results/perf/$(SIM_BACKEND)
PERF_RUNNER := $(if $(filter iverilog,$(SIM_BACKEND)),vvp,)

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
	cd "$(PROJECT_DIR)" && $(PERF_RUNNER) "$(PERF_ROOT)/single/bsim" 2> "$(PERF_RESULTS)/single.stderr.log" | tee "$(PERF_RESULTS)/single.log"

perf-stream:
	mkdir -p "$(PERF_RESULTS)"
	rm -f "$(PERF_RESULTS)/summary.json" "$(PERF_RESULTS)/stream.log" "$(PERF_RESULTS)/stream.stderr.log"
	$(MAKE) -C "$(PROJECT_DIR)" bsim SIM_BACKEND=$(SIM_BACKEND) \
		BSIM_TOP_SOURCE="$(PROJECT_DIR)/sim/TbSwayPerf.bsv" BSIM_TOP_MODULE=mkTbSwayPerf \
		BSIM_DIR="$(PERF_ROOT)/stream"
	cp "$(PERF_ROOT)/stream/mkTbSwayPerf.sched" "$(PERF_RESULTS)/stream.sched"
	cd "$(PROJECT_DIR)" && $(PERF_RUNNER) "$(PERF_ROOT)/stream/bsim" 2> "$(PERF_RESULTS)/stream.stderr.log" | tee "$(PERF_RESULTS)/stream.log"

check-perf-parser:
	cd "$(PROJECT_DIR)/reference" && $(PYTHON) -m unittest -v test_check_perf.py

# Boundary profiling reuses the same unstalled 64-frame driver. SWAY_PROFILE is
# enabled only in this isolated simulation build, never in the board build.
PROFILE_ROOT ?= $(PROJECT_DIR)/profile/$(SIM_BACKEND)
PROFILE_RESULTS ?= $(PROJECT_DIR)/results/profile/$(SIM_BACKEND)

.PHONY: profile check-profile-parser

profile:
	test -f "$(PERF_RESULTS)/stream.log" && test -f "$(PERF_RESULTS)/stream.stderr.log" && test -f "$(PERF_RESULTS)/stream.sched"
	mkdir -p "$(PROFILE_RESULTS)"
	rm -f "$(PROFILE_RESULTS)/summary.json" "$(PROFILE_RESULTS)/stages.csv" "$(PROFILE_RESULTS)/stream.log" "$(PROFILE_RESULTS)/stream.stderr.log"
	$(MAKE) -C "$(PROJECT_DIR)" bsim SIM_BACKEND=$(SIM_BACKEND) \
		BSCFLAGS_COMMON="$(BSCFLAGS_COMMON) -D SWAY_PROFILE" \
		BSIM_TOP_SOURCE="$(PROJECT_DIR)/sim/TbSwayPerf.bsv" BSIM_TOP_MODULE=mkTbSwayPerf \
		BSIM_DIR="$(PROFILE_ROOT)/stream"
	cp "$(PROFILE_ROOT)/stream/mkTbSwayPerf.sched" "$(PROFILE_RESULTS)/stream.sched"
	cd "$(PROJECT_DIR)" && $(PERF_RUNNER) "$(PROFILE_ROOT)/stream/bsim" 2> "$(PROFILE_RESULTS)/stream.stderr.log" | tee "$(PROFILE_RESULTS)/stream.log"
	cd "$(PROJECT_DIR)" && $(PYTHON) reference/check_profile.py \
		--log "$(PROFILE_RESULTS)/stream.log" --stderr "$(PROFILE_RESULTS)/stream.stderr.log" \
		--baseline-log "$(PERF_RESULTS)/stream.log" --baseline-stderr "$(PERF_RESULTS)/stream.stderr.log" \
		--backend "$(SIM_BACKEND)" \
		--baseline-schedule "$(PERF_RESULTS)/stream.sched" --profile-schedule "$(PROFILE_RESULTS)/stream.sched" \
		--output "$(PROFILE_RESULTS)/summary.json" --csv "$(PROFILE_RESULTS)/stages.csv"

check-profile-parser:
	cd "$(PROJECT_DIR)/reference" && $(PYTHON) -m unittest -v test_check_profile.py
