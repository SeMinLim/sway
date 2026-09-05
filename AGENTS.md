# Sway development rules

- Publish requested changes directly to `main` using the authenticated SeMinLim account.
- Commit author and committer must be Se-Min Lim <seminl1@kookmin.ac.kr>. Verify attribution after publishing. Never use github-actions[bot] to commit or push.
- CI may compile, test, and upload build artifacts with read-only repository permissions. CI must not modify Git history or push generated files.
- Implement hardware in Bluespec SystemVerilog and use SeMinLim/blueyosys for Bluesim, Verilog generation, Yosys synthesis, nextpnr-ecp5 placement/routing, and ecppack.
- Keep sway/observation_1 and blueyosys/projects/sway_observation_1 byte-identical for all project files. Preserve other blueyosys projects.
- Observation 1 has one baseline accelerator, one matched software reference, and testbenches, not two competing RTL architectures.
- The workload is the MambaLite-Micro KWS Mamba block. The baseline adopts eMamba's token-overlapped staged execution principle. Record source revisions and any departures. Do not claim an exact reproduction of the original eMamba hardware.
- Use FIFO-centered, explicitly staged BSV, tab indentation, named counters, explicit state reset, and SRAM terminology in documentation.
- Distinguish implementation tests, numerical equivalence, synthesis, place-and-route, board measurements, and task accuracy. Missing evidence is never PASS.
- Do not publish a bottleneck conclusion using an incomplete, intentionally serialized, or non-equivalent datapath.
