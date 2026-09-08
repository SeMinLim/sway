.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.NOTPARALLEL:
PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME := sway_observation_1/step4_projection_probe
# Identical file in both repositories; ROOTDIR may be overridden explicitly.
ROOTDIR ?= $(if $(wildcard $(PROJECT_DIR)/../../../build.mk),$(abspath $(PROJECT_DIR)/../../..),$(abspath $(PROJECT_DIR)/../../../blueyosys))
BOARD ?= ulx3s-85f
BSC ?= bsc
PYTHON ?= python3
SIM_BACKEND ?= iverilog
TOP_SOURCE := $(PROJECT_DIR)/bsv/ProjectionProbe.bsv
TOP_MODULE := mkProjectionProbe
BSIM_TOP_SOURCE := $(PROJECT_DIR)/sim/TbProjectionProbe.bsv
BSIM_TOP_MODULE := mkTbProjectionProbe
EXTRA_BSV_PATHS := $(PROJECT_DIR)/bsv $(PROJECT_DIR)/sim
NEEDS_INOUT_FIX := 0
BSIM_CPPFILES :=
BSIM_CXXFLAGS := -Xc++ -O0
# TRACE is simulation instrumentation, not part of the synthesis target.
DEBUGFLAGS += -D TRACE
PROBE_SEED ?= 7
PROBE_STALLED ?= 1
PROBE_SPECIAL ?= 0
BSC_RUNTIME_DIR ?= $(shell $(BSC) -help 2>/dev/null | sed -n 's/^Bluespec directory: //p')/Verilog
POST_RUN := $(PYTHON) reference/check_sim.py system.log fixtures/metadata.json results/$(SIM_BACKEND).json --backend $(SIM_BACKEND) --stderr output.log

ifeq ($(wildcard $(ROOTDIR)/build.mk),)
$(error blueYosys not found; set ROOTDIR=/absolute/path/to/blueyosys)
endif
# This is a diagnostic kernel, not a pin-constrained board top.
ifneq ($(filter pnr bitstream synth program,$(MAKECMDGOALS)),)
$(error Probe supports bsim/runsim/verilog/netlist only; no board top is supplied)
endif
include $(ROOTDIR)/build.mk

# Internal prerequisites only; public commands remain blueYosys commands.
.PHONY: probe-data probe-runtime
prepare bsim: probe-data
probe-data:
	@case "$(PROBE_STALLED):$(PROBE_SPECIAL)" in 0:0|0:1|1:0|1:1) ;; *) echo 'PROBE_STALLED and PROBE_SPECIAL must be 0 or 1' >&2; exit 2;; esac
	cd $(PROJECT_DIR) && mkdir -p results && rm -f results/$(SIM_BACKEND).json system.log output.log
	cd $(PROJECT_DIR) && $(PYTHON) reference/reference.py
	cd $(PROJECT_DIR) && $(PYTHON) reference/reference.py --fixture-dir fixtures --seed $(PROBE_SEED) $(if $(filter 1,$(PROBE_STALLED)),--stalled,) $(if $(filter 1,$(PROBE_SPECIAL)),--special,)

probe-runtime: verilog
	@test -f "$(BSC_RUNTIME_DIR)/BRAM2.v" || { echo 'Set BSC_RUNTIME_DIR to your bsc lib/Verilog directory' >&2; exit 2; }
	cp $(BSC_RUNTIME_DIR)/BRAM*.v $(BUILD_DIR)/
netlist: probe-runtime
