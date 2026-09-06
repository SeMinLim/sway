.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.NOTPARALLEL:
PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME := sway_observation_1
# Works unchanged in blueyosys/projects/sway_observation_1 and sway/observation_1.
ROOTDIR ?= $(if $(wildcard $(PROJECT_DIR)/../../build.mk),$(abspath $(PROJECT_DIR)/../..),$(abspath $(PROJECT_DIR)/../../blueyosys))
BOARD ?= ulx3s-85f
TOP_SOURCE := $(PROJECT_DIR)/bsv/Top.bsv
TOP_MODULE := mkTop
BSIM_TOP_SOURCE := $(PROJECT_DIR)/sim/TbSway.bsv
BSIM_TOP_MODULE := mkTbSway
EXTRA_BSV_PATHS := $(PROJECT_DIR)/bsv $(PROJECT_DIR)/sim
NEEDS_INOUT_FIX := 0
BSIM_CXXFLAGS := -Xc++ -O0
BSIM_CPPFILES :=
DATA_MODE ?= upstream
BSC_RUNTIME_DIR ?= $(shell $(BSC) -help 2>/dev/null | sed -n 's/^Bluespec directory: //p')/Verilog
# Reuse blueYosys runsim and its standard logs; keep validation in POST_RUN.
POST_RUN := grep -q '^SWAY_PASS ' system.log && ! grep -q 'SWAY_FAIL' system.log output.log

ifeq ($(wildcard $(ROOTDIR)/build.mk),)
$(error blueYosys not found at $(ROOTDIR). Set ROOTDIR=/absolute/path/to/blueyosys)
endif
include $(ROOTDIR)/build.mk

.PHONY: data link-data
# blueYosys owns BSV compilation, simulation, synthesis, P&R and bitstream generation.
prepare bsim: data
data:
	cd $(PROJECT_DIR) && python3 reference/prepare.py $(if $(filter fixture,$(DATA_MODE)),--fixture,)

# netlist runs from build/, while Bluesim runs from PROJECT_DIR.
# Explicit dependency prevents prepare from removing these files afterwards.
link-data: verilog
	ln -sfn $(PROJECT_DIR)/data $(BUILD_DIR)/data
	@test -f "$(BSC_RUNTIME_DIR)/BRAM1Load.v" || { echo 'Set BSC_RUNTIME_DIR to the compiler lib/Verilog directory' >&2; exit 2; }
	cp $(BSC_RUNTIME_DIR)/BRAM*.v $(BUILD_DIR)/
netlist: link-data
