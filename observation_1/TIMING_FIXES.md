# Observation 1 timing-path corrections

Base revisions: blueYosys `f181a5b95bafa150ed05119b8d127c6288a09b92`;
Sway `81bb81e470632f62028101d3531e40d76b185f0a`.

This change addresses the measured linear SRAM-response-to-multiplier path
and the five additional timing-risk paths identified in the code review.
It does not establish 100 MHz timing closure without a new routed result.

## Changes

| Path | Correction |
| --- | --- |
| Four linear engines: SRAM response to multiply | Request a two-cycle, registered SRAM read; capture the response and metadata in `operandsQ`; multiply in the next rule. |
| Linear accumulation to saturation/output storage | Keep the ordered signed48 feedback accumulator; enqueue completed row sums; perform the original conversion in a separate rule; write output registers in a third rule. |
| Linear input vector selection and bias selection | Use registered 8:1, 4:1, 4:1 selection. Apply the original bias scale and issue its aligned SRAM request only after the selected scalar is registered. Padding for smaller dimensions is constant and can be eliminated. |
| Softplus input selection to LUT address | Register the hierarchical selector and its final scalar before issuing the LUT read. |
| Softplus and paired convolution SiLU responses to output register banks | Use registered common-LUT reads and a separate response-capture FIFO before channel writeback. Release the input frame only after the last channel write. |
| UART serializer index calculation and 64-way vector read | Use a retained frame with a constant head-word tap and fixed one-word shifts. Bytes enter a registered FIFO; shifts occur only after the high byte is accepted into that FIFO. Top uses this tested `SwaySerializer` module. |

All linear engines still have four multiplication lanes. Weight files, memory
contents, tensor formats, signed48 accumulation, shift/saturation boundaries,
model dimensions and packet byte order are unchanged. The serializer keeps one
frame register rather than adding a second wide frame buffer. Existing build
commands, 100 MHz constraints, PLL configuration, UART RX and reset wiring are
unchanged. No workflow files are added.

SRAM registration is expressed as `cfg.latency = 2`; BSC generates
`BRAM1Load.PIPELINED = 1` in the inspected linear Verilog. Physical mapping and
timing still require Yosys and nextpnr. Pipeline cycle counts have changed;
previous profiling results must not be reused.

## Validation performed

BSC/Bluesim 2026.01, using the existing blueYosys `build.mk` and `runsim` command:

- Full kernel: the unchanged `TbSway` testbench passed on the explicitly
  synthetic fixture, with two 100-token sequences and 12,800 output values.
  The checks include sequence reset, tags, input gaps, prolonged output
  backpressure and token overlap. This was not a trained KWS accuracy test.
- Each of the four linear configurations: 64 synthetic frames compared against
  both the original RTL and an independently evaluated integer reference.
  All 30,976 scalar outputs matched. Nonzero bias, signed16 endpoints, tag
  wraparound, source gaps, a 24,000-cycle sink stall and operation counts were
  checked. A further 1,024 cycles checked for unexpected extra outputs.
- Softplus: every signed16 input, 65,536 values in 512 frames, matched the old
  RTL and LUT reference under the same stall and drain checks.
- Convolution and paired SiLU: 64 frames, 16,384 scalar outputs, matched old
  RTL and the independent reference, including repeated sequence resets.
- Serializer: 1,024 packets, 134,144 bytes and all 65,536 signed16 payload bit
  patterns matched Python little-endian packet encoding. Header flags, tag
  wraparound, backpressure and absence of trailing extra bytes were checked.
- Full hardware Top: BSC Verilog generation passed through the standard `pnr`
  build entry point. The flow then stopped at `check-yosys` because Yosys was
  not installed. Synthesis, placement/routing, routed frequency and board
  operation were not measured.

The current reset/PLL-interface and UART RX issues from the broader audit are
outside this timing-only change and are not marked fixed. Successful kernel
Bluesim does not validate board reset, clock-domain crossing or UART RX.

## Rebuild

With the normal blueYosys toolchain on PATH, use its existing commands:

```sh
make runsim PROJECT=sway_observation_1 BOARD=ulx3s-85f DATA_MODE=fixture
make runsim PROJECT=sway_observation_1 BOARD=ulx3s-85f
make pnr PROJECT=sway_observation_1 BOARD=ulx3s-85f
```

The default second command requires the pinned upstream model headers and
NumPy. The first command is intentionally synthetic. Neither simulation result
is a substitute for a passing post-route timing report.
