"""Validate the live nextpnr graph and every BEL before/after same-process route."""
import hashlib
import json
import os
from pathlib import Path

BASE = Path('/workspace/scratch/62d002c09fd9')
HOOK = BASE / 'verify_live_placement.py'
PACKED = BASE / 'lut-physical-recovered4/packed.json'
BELS = BASE / 'lut-physical-recovered4/observed_bels.json'
PHASE = os.environ.get('SWAY_LIVE_PHASE', 'pre-route')
OUTPUT = Path(os.environ.get('SWAY_LIVE_PROOF_OUTPUT', str(BASE / 'lut-physical-live/live-placement-proof.json')))
CLOCKS = {'$glbnet$clocks_pll_clk_100mhz': 100.0,
          '$glbnet$CLK_clk_25mhz$TRELLIS_IO_IN': 25.0}


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def mapping(proxy):
    entries = [(entry.first, entry.second) for entry in proxy]
    require(len({key for key, _ in entries}) == len(entries), 'Duplicate live map keys')
    return dict(entries)


def bit_boolean(value):
    require(isinstance(value, str) and value and set(value) <= {'0', '1'}, 'Malformed boolean property')
    return int(value, 2) != 0


report = {'status': 'fail', 'phase': PHASE,
          'scope': 'Live graph and placement identity only; final routing completion and clock timing are separate checks.',
          'input_paths': {'packed_graph': str(PACKED), 'observed_bels': str(BELS), 'hook': str(HOOK)},
          'input_sha256': {name: digest(path) for name, path in [('packed_graph', PACKED), ('observed_bels', BELS), ('hook', HOOK)]}}
try:
    require(PHASE in {'pre-route', 'post-route'}, 'Unexpected live proof phase')
    design = json.loads(PACKED.read_text())
    require(set(design['modules']) == {'top'}, 'Expected exactly one complete reference packed module')
    original = design['modules']['top']
    observed = json.loads(BELS.read_text())
    live_cells = mapping(ctx.cells)
    require(set(live_cells) == set(original['cells']) == set(observed), 'Live packed cell set differs')
    require(len(live_cells) == 104869, 'Unexpected live cell count')
    require(len({entry['bel'] for entry in observed.values()}) == len(observed), 'Reference BEL inventory overlaps')
    directions = {PORT_IN: 'input', PORT_OUT: 'output', PORT_INOUT: 'inout'}
    forward, reverse = {}, {}
    connected_pins = 0
    disconnected_pins = 0

    def pair(bits, net, label):
        global connected_pins, disconnected_pins
        require(len(bits) <= 1, 'Non-scalar packed primitive connection: ' + label)
        if not bits:
            require(net is None, 'Previously disconnected live pin is connected: ' + label)
            disconnected_pins += 1
            return
        require(type(bits[0]) is int and net is not None, 'Missing live connected pin: ' + label)
        old_wire, live_wire = bits[0], net.name
        require(isinstance(live_wire, str) and live_wire, 'Invalid live net name: ' + label)
        require(old_wire not in forward or forward[old_wire] == live_wire, 'Reference net split: ' + label)
        require(live_wire not in reverse or reverse[live_wire] == old_wire, 'Reference nets merged: ' + label)
        forward[old_wire], reverse[live_wire] = live_wire, old_wire
        connected_pins += 1

    actual_bels = {}
    for name, original_cell in original['cells'].items():
        live = live_cells[name]
        require(live.name == name and live.type == original_cell['type'] == observed[name]['type'], 'Live cell identity/type differs: ' + name)
        require(mapping(live.params) == original_cell['parameters'], 'Live parameters differ: ' + name)
        require(live.bel == observed[name]['bel'], 'Live BEL differs: ' + name)
        actual_bels[name] = live.bel
        ports = mapping(live.ports)
        require(set(ports) == set(original_cell['connections']) == set(original_cell['port_directions']), 'Live primitive port set differs: ' + name)
        for pin, values in original_cell['connections'].items():
            port = ports[pin]
            require(port.name == pin and directions[port.type] == original_cell['port_directions'][pin], 'Live primitive port direction/name differs: ' + name + '.' + pin)
            pair(values, port.net, name + '.' + pin)
    require(len(set(actual_bels.values())) == len(live_cells), 'Live BELs overlap')
    expected_ports = {}
    for name, port in original['ports'].items():
        require(not port.get('upto', False), 'Unexpected reversed reference top bus')
        offset = port.get('offset', 0)
        if len(port['bits']) == 1 and offset == 0:
            expected_ports[name] = (port['direction'], port['bits'])
        else:
            for index, bit in enumerate(port['bits']):
                expected_ports[f'{name}[{index + offset}]'] = (port['direction'], [bit])
    io_names = {name for name, cell in original['cells'].items() if cell['type'] == 'TRELLIS_IO'}
    require(io_names == {name + '$tr_io' for name in expected_ports}, 'Top-level IO cell set differs')
    for name, (direction, bits) in expected_ports.items():
        cell_name = name + '$tr_io'
        reference_io = original['cells'][cell_name]
        require(reference_io['parameters']['DIR'] == direction.upper(), 'Reference IO direction differs: ' + name)
        require(reference_io['connections']['B'] == bits, 'Reference IO pad does not implement its top port: ' + name)
        live_io = live_cells[cell_name]
        pad = live_io.ports['B'].net
        require(pad is not None and pad.name == name and forward[bits[0]] == pad.name,
                'Live top-level IO pad net differs: ' + name)
    require(len(forward) == len(reverse) == 123513, 'Unexpected graph bijection size')
    report['topology'] = {'status': 'pass', 'cells': len(live_cells), 'bijective_wires': len(forward),
                          'top_scalar_io_pads': len(expected_ports), 'connected_port_entries': connected_pins,
                          'disconnected_port_entries': disconnected_pins,
                          'comparison': 'Exact named cell/type/parameters/primitive port shape/direction/connectivity, plus all eleven top IO cell directions/pad nets/BELs, with bijective reference-wire to live-net-name mapping',
                          'top_interface_evidence': 'Pinned live API omits ctx.ports. Reference top-port metadata is bound to its exact TRELLIS_IO DIR/B connection; all eleven live pad net names and physical IO locations match.'}
    report['placement'] = {'status': 'pass', 'exact_bel_count': len(actual_bels), 'unique_bels': len(set(actual_bels.values())),
                           'no_cell_moved': True, 'actual_bels_sha256': hashlib.sha256(json.dumps(actual_bels, sort_keys=True).encode()).hexdigest()}
    report['physical_settings_evidence'] = 'Pinned live API omits ctx.settings. Runner must bind the explicit same-process CLI and strict final 100/25 MHz timing; this hook does not claim a settings snapshot.'
    require(all(name in ctx.nets for name in CLOCKS), 'Missing live global clock net')
    if PHASE == 'pre-route':
        for name, frequency in CLOCKS.items():
            ctx.addClock(name, frequency)
    report['clock_targets_mhz'] = CLOCKS
    report['clock_action'] = 'Restore exact global clocks before routing' if PHASE == 'pre-route' else 'No clock mutation after routing; final timing report must match pre-route targets'
    require(all(digest(Path(report['input_paths'][key])) == value for key, value in report['input_sha256'].items()), 'Live proof input changed')
    report['inputs_unchanged'] = True
    report['status'] = 'pass'
except Exception as exc:
    report['error'] = str(exc)
    raise
finally:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + '\n')
    print('SWAY_LIVE_PLACEMENT_' + report['status'].upper() + ' phase=' + PHASE + ' report=' + str(OUTPUT), flush=True)
