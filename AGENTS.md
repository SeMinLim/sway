# Sway development rules

- Publish requested changes directly to `main` using the authenticated SeMinLim account.
- Commit author and committer must be Se-Min Lim <seminl1@kookmin.ac.kr>. Verify attribution after publishing. Never use github-actions[bot] to commit or push.
- Do not create new `.md` files without the user's explicit permission. Do not create `.gitignore` files.
- Keep READMEs concise and reader-oriented: explain the purpose, essential configuration, build/run commands, outputs, and current validation status. Omit change histories, repeated caveats, and unnecessary implementation detail.
- Use only the public build commands documented in the blueYosys README's How to build section. Reuse build.mk and do not add custom public targets such as test. Simulation uses bsim/runsim and the existing POST_RUN hook.
- Do not create GitHub Actions workflows unless explicitly requested. Remove task-created workflow files when the task is finished. Never use CI to commit or push generated files.
- Implement hardware in Bluespec SystemVerilog and use SeMinLim/blueyosys for Bluesim, Verilog generation, Yosys synthesis, nextpnr-ecp5 placement/routing, and ecppack.
- Keep sway/observation_1 and blueyosys/projects/sway_observation_1 byte-identical for all project files. Preserve other blueyosys projects.
- Observation 1 has one baseline accelerator, one matched software reference, and testbenches, not two competing RTL architectures.
- The workload is the MambaLite-Micro KWS Mamba block. The baseline adopts eMamba's token-overlapped staged execution principle. Record source revisions and any departures. Do not claim an exact reproduction of the original eMamba hardware.
- Use FIFO-centered, explicitly staged BSV, tab indentation, named counters, explicit state reset, and SRAM terminology in documentation.
- Distinguish implementation tests, numerical equivalence, synthesis, place-and-route, board measurements, and task accuracy. Missing evidence is never PASS.
- Do not publish a bottleneck conclusion using an incomplete, intentionally serialized, or non-equivalent datapath.
