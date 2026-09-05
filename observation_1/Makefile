.DEFAULT_GOAL := help
SHELL := /bin/bash
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

ifeq ($(wildcard $(ROOTDIR)/build.mk),)
$(error blueYosys not found at $(ROOTDIR). Set ROOTDIR=/absolute/path/to/blueyosys)
endif
include $(ROOTDIR)/build.mk

.PHONY: data link-data test
# blueYosys owns BSV compilation, synthesis, P&R and bitstream generation.
prepare bsim: data
data:
	cd $(PROJECT_DIR) && python3 reference/prepare.py $(if $(filter fixture,$(DATA_MODE)),--fixture,)

# netlist runs from build/, while Bluesim runs from PROJECT_DIR.
# Explicit dependency prevents prepare from removing the link afterwards.
link-data: verilog
	ln -sfn $(PROJECT_DIR)/data $(BUILD_DIR)/data
netlist: link-data

# Fail on simulator errors or absent PASS, not just on tee's return status.
test: bsim
	cd $(PROJECT_DIR) && set -o pipefail; $(BSIM_DIR)/bsim 2>&1 | tee simulation.log
	@grep -q 'SWAY_PASS' $(PROJECT_DIR)/simulation.log
	@! grep -q 'SWAY_FAIL' $(PROJECT_DIR)/simulation.log
