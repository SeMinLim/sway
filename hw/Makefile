.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.NOTPARALLEL:
PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME := sway_mars
INPUT_PROJECTION_VARIANT ?= B0
ifneq ($(filter $(INPUT_PROJECTION_VARIANT),B0 B1 B2),$(INPUT_PROJECTION_VARIANT))
$(error INPUT_PROJECTION_VARIANT must be B0, B1, or B2)
endif
# Frozen B0/B1 packages preserve the RTL used by their measured builds. B2 uses
# the current package; selecting only flags on that file changes generated RTL.
VARIANT_SUFFIX := $(if $(filter B0,$(INPUT_PROJECTION_VARIANT)),,-$(INPUT_PROJECTION_VARIANT))
LINEAR_VARIANT_PATH := $(if $(filter B0 B1,$(INPUT_PROJECTION_VARIANT)),$(PROJECT_DIR)/variants/$(INPUT_PROJECTION_VARIANT),)
LINEAR_SOURCE := $(if $(LINEAR_VARIANT_PATH),$(LINEAR_VARIANT_PATH),$(PROJECT_DIR)/bsv)/SwayLinear.bsv
RESULTS_DIR ?= $(PROJECT_DIR)/results$(VARIANT_SUFFIX)
BUILD_DIR ?= $(PROJECT_DIR)/build$(VARIANT_SUFFIX)
BSIM_DIR ?= $(PROJECT_DIR)/bsim$(VARIANT_SUFFIX)
BLUEYOSYS ?= $(abspath $(PROJECT_DIR)/../../blueyosys)
ROOTDIR := $(BLUEYOSYS)
BOARD ?= ulx3s-85f
TOP_SOURCE := $(PROJECT_DIR)/bsv/Top.bsv
TOP_MODULE := mkTop
BSIM_TOP_SOURCE := $(PROJECT_DIR)/sim/TbSway.bsv
BSIM_TOP_MODULE := mkTbSway
EXTRA_BSV_PATHS := $(LINEAR_VARIANT_PATH) $(PROJECT_DIR)/bsv $(PROJECT_DIR)/generated $(PROJECT_DIR)/sim
NEEDS_INOUT_FIX := 0
BSIM_CPPFILES :=
BSIM_CXXFLAGS := -Xc++ -O1
POST_RUN = $(PYTHON) reference/check_sim.py system.log --output $(RESULTS_DIR)/$(SIM_BACKEND).json --backend $(SIM_BACKEND) --stderr output.log && cp system.log $(RESULTS_DIR)/$(SIM_BACKEND).log
BSC_RUNTIME_DIR ?= $(shell $(BSC) -help 2>/dev/null | sed -n 's/^Bluespec directory: //p')/Verilog
YOSYS_DATA_DIR ?= $(shell yosys-config --datdir 2>/dev/null)
RESET_SIM_DIR ?= $(PROJECT_DIR)/reset-sim$(VARIANT_SUFFIX)
VERILATOR_RTL_DIR ?= $(PROJECT_DIR)/verilator-rtl$(VARIANT_SUFFIX)
VERILATOR_SIM_DIR ?= $(PROJECT_DIR)/verilator$(VARIANT_SUFFIX)
VERILATOR_JOBS ?= 4

ifeq ($(wildcard $(ROOTDIR)/build.mk),)
$(error blueYosys not found at $(ROOTDIR). Set BLUEYOSYS=/absolute/path/to/blueyosys)
endif
include $(ROOTDIR)/build.mk
ifneq ($(filter B1 B2,$(INPUT_PROJECTION_VARIANT)),)
NATIVE_ROM_CHECK_FLAGS += --input-projection-fifo2
ifeq ($(findstring SWAY_INPUT_FIFO2,$(BSCFLAGS_COMMON)),)
override BSCFLAGS_COMMON += -D SWAY_INPUT_FIFO2
endif
endif
ifeq ($(INPUT_PROJECTION_VARIANT),B2)
ifeq ($(findstring SWAY_INPUT_OVERLAP,$(BSCFLAGS_COMMON)),)
override BSCFLAGS_COMMON += -D SWAY_INPUT_OVERLAP
endif
BLOCK_PROFILE_CHECK_FLAGS += --group-overlap
endif
RTL_DIRS += $(PROJECT_DIR)/rtl
BOARD_YOSYS_FLAGS += -nodsp
BOARD_PNR_FLAGS += --router router1 --placer-heap-cell-placement-timeout 0 --tmg-ripup

ifeq ($(SIM_BACKEND),iverilog)
override BSCFLAGS_COMMON += -D SWAY_ROM_NATIVE
BSC := $(BSC) -vsearch +:$(PROJECT_DIR)/rtl -Xv -DSWAY_ROM_BEHAVIORAL -Xv -I$(PROJECT_DIR)/generated
endif

.PHONY: runtime-rtl
runtime-rtl: verilog
	test -f "$(BSC_RUNTIME_DIR)/ResetInverter.v"
	test -f "$(BSC_RUNTIME_DIR)/RegFile.v"
	test -f "$(BSC_RUNTIME_DIR)/RegFileLoad.v"
	cp "$(BSC_RUNTIME_DIR)/ResetInverter.v" "$(BSC_RUNTIME_DIR)/RegFile.v" "$(BSC_RUNTIME_DIR)/RegFileLoad.v" "$(BUILD_DIR)/"
	cp "$(PROJECT_DIR)/generated/SwayCoeffRomInit.vh" "$(BUILD_DIR)/"
	mkdir -p "$(BUILD_DIR)/generated"
	cp "$(PROJECT_DIR)"/generated/linear_l*_lane*.hex "$(BUILD_DIR)/generated/"
netlist: runtime-rtl

PHYSICAL_NETLIST := $(BUILD_DIR)/$(TOP_MODULE).physical.json
.PHONY: physical-netlist
physical-netlist: netlist
	mkdir -p "$(RESULTS_DIR)"
	$(PYTHON) "$(PROJECT_DIR)/reference/check_native_rom.py" --netlist "$(JSON_NETLIST)" --output "$(RESULTS_DIR)/native_rom.json" $(NATIVE_ROM_CHECK_FLAGS)
	rm -f "$(PHYSICAL_NETLIST)" "$(RESULTS_DIR)/control_replication.json" "$(RESULTS_DIR)/control_replication_audit.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/replicate_controls.py" --input "$(JSON_NETLIST)" --output "$(PHYSICAL_NETLIST)" --manifest "$(RESULTS_DIR)/control_replication.json" --top "$(TOP_MODULE)" --cap 64
	$(PYTHON) "$(PROJECT_DIR)/reference/check_control_replication.py" --input "$(JSON_NETLIST)" --output "$(PHYSICAL_NETLIST)" --manifest "$(RESULTS_DIR)/control_replication.json" --report "$(RESULTS_DIR)/control_replication_audit.json"
# The existing blueyosys pnr recipe consumes the audited physical netlist;
# private prevents this override from changing its synthesis prerequisites.
pnr: private JSON_NETLIST := $(PHYSICAL_NETLIST)
pnr: physical-netlist

.PHONY: check-timing
check-timing: bitstream
	$(PYTHON) "$(PROJECT_DIR)/reference/check_timing.py" --report "$(PNR_REPORT_JSON)" --log "$(PNR_LOG)" --bitstream "$(BITSTREAM)" --output "$(RESULTS_DIR)/timing.json"
synth: check-timing

.PHONY: check-verilator
check-verilator:
	$(MAKE) -C "$(PROJECT_DIR)" bsim SIM_BACKEND=iverilog BSIM_DIR="$(VERILATOR_RTL_DIR)"
	$(PYTHON) "$(PROJECT_DIR)/reference/run_verilator.py" --rtl "$(VERILATOR_RTL_DIR)/mkTbSway.v" --build-dir "$(VERILATOR_SIM_DIR)" --results-dir "$(RESULTS_DIR)" --bsc-runtime "$(BSC_RUNTIME_DIR)" --jobs "$(VERILATOR_JOBS)"

.PHONY: check-reset
check-reset: check-bsc
	mkdir -p "$(RESET_SIM_DIR)/bsim" "$(RESULTS_DIR)"
	iverilog -g2012 -I "$(YOSYS_DATA_DIR)/lattice" -s TestLocalReset -o "$(RESET_SIM_DIR)/reset.vvp" "$(PROJECT_DIR)/sim/TestLocalReset.v" "$(PROJECT_DIR)/rtl/SwayReset.v" "$(BSC_RUNTIME_DIR)/SyncResetA.v" "$(YOSYS_DATA_DIR)/lattice/common_sim.vh"
	vvp "$(RESET_SIM_DIR)/reset.vvp" | tee "$(RESULTS_DIR)/reset.log"
	$(PYTHON) -c 'from pathlib import Path; import sys; s=Path(sys.argv[1]).read_text(); sys.exit(0 if "SWAY_RESET_PASS" in s and "FAIL" not in s else 1)' "$(RESULTS_DIR)/reset.log"
	$(BSC) -D BSIM -sim -u -bdir "$(RESET_SIM_DIR)/bsim" -simdir "$(RESET_SIM_DIR)/bsim" -p +:$(BSV_PATH) -g mkTbLocalReset "$(PROJECT_DIR)/sim/TbLocalReset.bsv"
	$(BSC) -D BSIM -sim -e mkTbLocalReset -bdir "$(RESET_SIM_DIR)/bsim" -simdir "$(RESET_SIM_DIR)/bsim" -o "$(RESET_SIM_DIR)/bsim/check"
	"$(RESET_SIM_DIR)/bsim/check" | tee "$(RESULTS_DIR)/reset_bluesim.log"
	$(PYTHON) -c 'from pathlib import Path; import sys; s=Path(sys.argv[1]).read_text(); sys.exit(0 if "SWAY_RESET_BSIM_PASS" in s and "FAIL" not in s else 1)' "$(RESULTS_DIR)/reset_bluesim.log"

include $(PROJECT_DIR)/perf.mk
