import re

def inspect_operand_control(rtl, expected_operands=None):
    """Inspect BSC continuous-assignment cones at native DSP operand ports.

    Keep write-presence signals outside the operand cones: the compiler may
    share those signals with valid-bit generation. Unresolved identifiers are
    reported as leaves; this function does not parse procedural always blocks.
    The focused test has direct assignments from registered ROM data outputs.
    """
    names = sorted(set(re.findall(r"(?<![\w$])[\w$]*[ab]Wire[$_]whas\b", rtl)))
    drivers = dict(re.findall(r"\bassign\s+([\w$]+)\s*=\s*(.*?);", rtl, re.S))
    ports = re.findall(r"\.dataa[xy]\s*\(\s*([\w$]+)\s*\)", rtl)
    cones = {}
    for signal in ports:
        pending, seen = [signal], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in drivers:
                pending.extend(re.findall(r"[A-Za-z_][\w$]*", drivers[current]))
        cones[signal] = {
            "signals": sorted(seen),
            "assignments": {s: drivers[s] for s in sorted(seen) if s in drivers},
            "leaf_identifiers": sorted(seen - drivers.keys()),
            "write_presence_dependencies": sorted(set(names) & seen),
        }
    report = {
        "status": "pass",
        "operand_ports": ports,
        "operand_assignments": [
            "assign " + signal + " = " + drivers[signal] + ";"
            for signal in ports if signal in drivers
        ],
        "operand_dependency_cones": cones,
        "retained_write_presence_signals": names,
        "retained_write_presence_uses": [
            line.strip() for line in rtl.splitlines()
            if any(name in line for name in names)
        ],
        "scope": "Continuous-assignment operand cones; dollar and underscore identifiers supported. Presence signals outside operand cones are permitted and reported.",
    }
    assert ports and all(signal in drivers for signal in ports), "Operand drivers missing"
    if expected_operands is not None:
        assert len(ports) == expected_operands, "Unexpected operand port count"
    assert all(not cone["write_presence_dependencies"] for cone in cones.values()), "Operand data cone depends on write-presence control"
    return report
