.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.NOTPARALLEL:
PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME := sway_mars
BLUEYOSYS ?= $(abspath $(PROJECT_DIR)/../../blueyosys)
ROOTDIR := $(BLUEYOSYS)
BOARD ?= ulx3s-85f
TOP_SOURCE := $(PROJECT_DIR)/bsv/Top.bsv
TOP_MODULE := mkTop
BSIM_TOP_SOURCE := $(PROJECT_DIR)/sim/TbSway.bsv
BSIM_TOP_MODULE := mkTbSway
EXTRA_BSV_PATHS := $(PROJECT_DIR)/bsv $(PROJECT_DIR)/generated $(PROJECT_DIR)/sim
NEEDS_INOUT_FIX := 0
BSIM_CPPFILES :=
BSIM_CXXFLAGS := -Xc++ -O1
POST_RUN = $(PYTHON) reference/check_sim.py system.log --output results/$(SIM_BACKEND).json --backend $(SIM_BACKEND) --stderr output.log
BSC_RUNTIME_DIR ?= $(shell $(BSC) -help 2>/dev/null | sed -n 's/^Bluespec directory: //p')/Verilog

ifeq ($(wildcard $(ROOTDIR)/build.mk),)
$(error blueYosys not found at $(ROOTDIR). Set BLUEYOSYS=/absolute/path/to/blueyosys)
endif
include $(ROOTDIR)/build.mk

.PHONY: runtime-rtl
runtime-rtl: verilog
	test -f "$(BSC_RUNTIME_DIR)/ResetInverter.v"
	test -f "$(BSC_RUNTIME_DIR)/RegFile.v"
	cp "$(BSC_RUNTIME_DIR)/ResetInverter.v" "$(BSC_RUNTIME_DIR)/RegFile.v" "$(BUILD_DIR)/"
netlist: runtime-rtl
