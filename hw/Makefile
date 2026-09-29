.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.NOTPARALLEL:

PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME := sway_observation
# Resolve unchanged in blueyosys/projects/sway_observation or a sibling Sway checkout.
ROOTDIR ?= $(if $(wildcard $(PROJECT_DIR)/../../build.mk),$(abspath $(PROJECT_DIR)/../..),$(abspath $(PROJECT_DIR)/../../blueyosys))
BOARD ?= ulx3s-85f
RESULTS_DIR ?= $(PROJECT_DIR)/results

# Project Top preserves the baseline's PLL reset polarity and release sequence.
# Simulation drives the same baseline core directly with local golden fixtures.
TOP_SOURCE := $(PROJECT_DIR)/Top.bsv
TOP_MODULE := mkTop
NEEDS_INOUT_FIX := 0
BSIM_TOP_SOURCE := $(PROJECT_DIR)/sim/TbSway.bsv
BSIM_TOP_MODULE := mkTbSway
EXTRA_BSV_PATHS := $(PROJECT_DIR) $(PROJECT_DIR)/bsv $(PROJECT_DIR)/generated $(PROJECT_DIR)/sim
BSIM_CPPFILES :=
BSIM_CXXFLAGS := -Xc++ -O1
POST_RUN = $(PYTHON) "$(PROJECT_DIR)/reference/check_sim.py" system.log --output "$(RESULTS_DIR)/$(SIM_BACKEND).json" --backend "$(SIM_BACKEND)" --stderr output.log && cp system.log "$(RESULTS_DIR)/$(SIM_BACKEND).log"
BSC_RUNTIME_DIR ?= $(shell $(BSC) -help 2>/dev/null | sed -n 's/^Bluespec directory: //p')/Verilog
# Use the primitive models belonging to the selected Yosys installation.
YOSYS_CELLS_SIM ?= $(shell \
 executable=$$(command -v "$(firstword $(YOSYS))"); \
 config="$$(dirname "$$executable")/yosys-config"; \
 if test -n "$$executable" && test -x "$$config"; then \
  printf '%s/ecp5/cells_sim.v' "$$("$$config" --datdir)"; \
 elif test "$$(readlink -f "$$executable" 2>/dev/null)" = /usr/bin/yosys; then \
  printf '%s' /usr/share/yosys/ecp5/cells_sim.v; \
 fi)

ifeq ($(wildcard $(ROOTDIR)/build.mk),)
$(error blueYosys not found at $(ROOTDIR). Set ROOTDIR=/absolute/path/to/blueyosys)
endif
include $(ROOTDIR)/build.mk

# Yosys 0.67 defaults to ABC9; 0.33 already uses legacy ABC and has no flag.
# synth_ecp5 delegates its help to synth_lattice in current Yosys releases.
YOSYS_NOABC9 := $(if $(findstring -noabc9,$(shell $(YOSYS) -Q -T -p 'help synth_ecp5; help synth_lattice' 2>/dev/null)),-noabc9)
override BOARD_YOSYS_FLAGS += $(YOSYS_NOABC9)

# Keep the normal build and the physical verification runner on the same flow.
BOARD_PNR_FLAGS += --router router1 --speed 6 --seed 1 --freq 100
BOARD_PNR_FLAGS += --tmg-ripup
# nextpnr 2b560ad0 squares the cell count in a signed int in this retry guard.
# Zero disables that guard, while placement legality and strict timing remain.
BOARD_PNR_FLAGS += --placer-heap-cell-placement-timeout 0
BOARD_PNR_FLAGS += --pre-place "$(PROJECT_DIR)/reference/place_core_reset.py"

# The BSC Icarus link script reads this environment variable for local primitives.
export BSC_VSIM_FLAGS += -y $(PROJECT_DIR)/rtl

# Icarus executables embed their installation-time vvp path. Invoke the runtime
# from PATH so copied projects also work with user-local tool installations.
ifeq ($(SIM_BACKEND),iverilog)
.PHONY: sim-runner
sim-runner: bsim
	mv "$(BSIM_DIR)/bsim" "$(BSIM_DIR)/bsim.vvp"
	printf '%s\n' '#!/bin/sh' 'exec vvp "$$(dirname "$$0")/bsim.vvp" "$$@"' > "$(BSIM_DIR)/bsim"
	chmod +x "$(BSIM_DIR)/bsim"
runsim: sim-runner
endif

# Baseline scan state and PLL reset conversion use these compiler primitives.
# blueYosys copies its common runtime RTL; add the two missing files for Yosys.
.PHONY: runtime-rtl
runtime-rtl: verilog
	test -f "$(BSC_RUNTIME_DIR)/RegFile.v"
	test -f "$(BSC_RUNTIME_DIR)/ResetInverter.v"
	cp "$(BSC_RUNTIME_DIR)/RegFile.v" "$(BSC_RUNTIME_DIR)/ResetInverter.v" "$(BSC_RUNTIME_DIR)/FIFOL1.v" "$(BUILD_DIR)/"
	cp "$(PROJECT_DIR)/rtl/"*.v "$(BUILD_DIR)/"
netlist: runtime-rtl

# ECP5's eight PFU FFs share LSR. Merge identical reset-only LUTs after mapping;
# arbitrary LUT merging would invalidate dedicated PFUMX/L6MUX21 structures.
.PHONY: physical-netlist
physical-netlist: netlist
	test -n "$(YOSYS_CELLS_SIM)" -a -f "$(YOSYS_CELLS_SIM)"
	$(PYTHON) "$(PROJECT_DIR)/reference/merge_reset_controls.py" "$(JSON_NETLIST)" --report "$(BUILD_DIR)/reset_merge.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/replicate_rom_address.py" "$(JSON_NETLIST)" --rtl "$(BUILD_DIR)/mkTop.v" --before "$(BUILD_DIR)/mkTop.before_address_replicas.json" --report "$(BUILD_DIR)/address_replicas.json"
	# Each coefficient output group gets its own purely combinational page decode.
	# Preserve the 56-address-copy netlist so both transformation stages are audited.
	$(PYTHON) "$(PROJECT_DIR)/reference/replicate_page_decoders.py" "$(JSON_NETLIST)" --before "$(BUILD_DIR)/mkTop.before_page_decoders.json" --rtl "$(BUILD_DIR)/mkTop.v" --report "$(BUILD_DIR)/page_decoders.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/check_page_decoder_copies.py" --before "$(BUILD_DIR)/mkTop.before_page_decoders.json" --after "$(JSON_NETLIST)" --rtl "$(BUILD_DIR)/mkTop.v" --address-auditor "$(PROJECT_DIR)/reference/check_address_replicas.py" --report "$(BUILD_DIR)/page_decoder_audit.json"
	# Preserve the page-audited boundary; private ROM replacement audits before writing.
	cp "$(JSON_NETLIST)" "$(BUILD_DIR)/mkTop.before_private_rom.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/replicate_private_rom.py" --before "$(BUILD_DIR)/mkTop.before_private_rom.json" --weights "$(PROJECT_DIR)/model/export/headHidden_weight.bin" --output "$(JSON_NETLIST)" --rtl "$(BUILD_DIR)/mkTop.v" --cells-sim "$(YOSYS_CELLS_SIM)" --report "$(BUILD_DIR)/private_rom.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/check_private_rom.py" --before "$(BUILD_DIR)/mkTop.before_private_rom.json" --after "$(JSON_NETLIST)" --weights "$(PROJECT_DIR)/model/export/headHidden_weight.bin" --rtl "$(BUILD_DIR)/mkTop.v" --cells-sim "$(YOSYS_CELLS_SIM)" --report "$(BUILD_DIR)/private_rom_audit.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/check_lut_mapping.py" --netlist "$(JSON_NETLIST)" --rtl "$(BUILD_DIR)/mkTop.v" --output "$(BUILD_DIR)/lut_mapping.json"
	$(PYTHON) "$(PROJECT_DIR)/reference/check_mapped_weights.py" --netlist "$(JSON_NETLIST)" --rtl "$(BUILD_DIR)/mkTop.v" --export "$(PROJECT_DIR)/model/export" --cells-sim "$(YOSYS_CELLS_SIM)" --output "$(BUILD_DIR)/mapped_weights.json"

pnr: physical-netlist
