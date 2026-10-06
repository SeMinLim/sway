#!/usr/bin/env python3
"""Count live affine accumulation circuits in a supplied ECP5 mapped netlist."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def without_timestamp(text):
    return re.sub(r'^// On .*\n', '', text, flags=re.M)


def find_adders(source):
    pattern = re.compile(
        r'assign (MUX_(main_core_(?:block[01]_(?:bProjection|cProjection|'
        r'deltaInputProjection|deltaProjection_engine|gateProjection|mainProjection|'
        r'outputProjection_engine)|embedding_engine|headHidden_engine|headOutput_engine))'
        r'_sumR_write_1__VAL_1)\s*=\s*(.*?);', re.S)
    assignments = pattern.findall(source)
    require(len(assignments) == 17, 'Expected 17 affine accumulation expressions')
    require(len({stage for _, stage, _ in assignments}) == 17,
            'Affine stages must be distinct')
    units = []
    for output, stage, expression in assignments:
        aliases = set(re.findall(r'main_core_\w+__q\d+', expression))
        require(len(aliases) == 1, f'{stage}: expected one product alias')
        alias = aliases.pop()
        expected = stage + '_sumR+{{8{' + alias + '[15]}},' + alias + '}'
        require(re.sub(r'\s+', '', expression) == expected,
                f'{stage}: accumulation expression changed')
        match = re.search(r'assign\s+' + re.escape(alias) + r'\s*=\s*(.*?);', source, re.S)
        require(match is not None and re.sub(r'\s+', '', match.group(1)) ==
                stage + '_productQ_D_OUT[16:1]', f'{stage}: product alias changed')
        units.append({'stage': stage, 'width': 24, 'input_product_width': 16,
                      'result_wire': output, 'rtl_expression': expression.strip(),
                      'product_alias': alias})
    return units


def check_chains(module, units, require_state=True):
    cells, nets = module['cells'], module['netnames']
    drivers = collections.defaultdict(list)
    consumers = collections.defaultdict(list)
    for name, cell in cells.items():
        for port, bits in cell['connections'].items():
            destination = drivers if cell['port_directions'][port] == 'output' else consumers
            for bit in bits:
                if isinstance(bit, int):
                    destination[bit].append((name, port))
    for name, port in module.get('ports', {}).items():
        if port['direction'] == 'output':
            for bit in port['bits']:
                consumers[bit].append(('<top>', name))
    previous_cells = set()
    results = []
    for unit in units:
        stage = unit['stage']
        state = nets[stage + '_sumR']['bits']
        queued_product = nets[stage + '_productQ_D_OUT']['bits']
        require(len(queued_product) == 17, f'{stage}: product queue width changed')
        product = queued_product[1:17]
        extended = product + [product[-1]] * 8
        result = nets[unit['result_wire']]['bits']
        require(len(state) == len(result) == 24, f'{stage}: adder width changed')
        require(all(isinstance(bit, int) for bit in state + product + result),
                f'{stage}: constant operand or result bit')
        chain = []
        carry = '0'
        for bit in range(0, 24, 2):
            require(len(drivers[result[bit]]) == len(drivers[result[bit + 1]]) == 1,
                    f'{stage}: missing or multiple result drivers at bit {bit}')
            name0, port0 = drivers[result[bit]][0]
            name1, port1 = drivers[result[bit + 1]][0]
            require(name0 == name1 and port0 == 'S0' and port1 == 'S1',
                    f'{stage}: expected two-bit carry cell at bit {bit}')
            cell = cells[name0]
            require(cell['type'] == 'CCU2C', f'{stage}: expected CCU2C')
            require(cell['parameters'] == {
                'INIT0': '1001011010101010', 'INIT1': '1001011010101010',
                'INJECT1_0': 'NO', 'INJECT1_1': 'NO'},
                f'{stage}: carry-cell arithmetic parameters changed')
            ports = cell['connections']
            require(ports['CIN'] == [carry], f'{stage}: broken carry chain')
            for half in (0, 1):
                suffix = str(half)
                require(ports['A' + suffix] == [state[bit + half]] and
                        ports['B' + suffix] == [extended[bit + half]] and
                        ports['C' + suffix] == ['0'] and ports['D' + suffix] == ['1'],
                        f'{stage}: accumulation operand mismatch at bit {bit + half}')
            carry = ports['COUT'][0]
            chain.append(name0)
        require(len(set(chain)) == 12, f'{stage}: repeated carry cell')
        require(not (set(chain) & previous_cells), f'{stage}: merged accumulation chain')
        previous_cells.update(chain)
        require(all(consumers[bit] for bit in result), f'{stage}: unused result bits')
        record = dict(unit)
        record.update({
            'arithmetic': 'sumR + sign_extend(productQ.D_OUT[16:1]); modulo 2^24',
            'operation_rules': ['WILL_FIRE_RL_' + stage + '_process3',
                                'WILL_FIRE_RL_' + stage + '_process3Last'],
            'mapped_carry_cells_in_bit_order': chain,
            'result_bits_have_consumers': True, 'disjoint_carry_chain': True,
        })
        if require_state:
            state_drivers = [drivers[bit] for bit in state]
            require(all(len(items) == 1 and cells[items[0][0]]['type'] == 'TRELLIS_FF'
                        for items in state_drivers), f'{stage}: sumR is not retained in FFs')
            record['sum_state_flipflops'] = [items[0][0] for items in state_drivers]
        results.append(record)
    return results, len(previous_cells)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl', type=Path, required=True, help='Current generated mkTop Verilog')
    parser.add_argument('--mapped-rtl', type=Path, help='Verilog used to produce the netlist; defaults to --rtl')
    parser.add_argument('--netlist', type=Path, required=True, help='Whole-baseline mapped Yosys JSON')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mapping-label', default='Caller-supplied whole-baseline ECP5 mapping')
    args = parser.parse_args()
    mapped_rtl = args.mapped_rtl or args.rtl
    source = args.rtl.read_text()
    require(without_timestamp(mapped_rtl.read_text()) == without_timestamp(source),
            'Current RTL differs from mapped-input RTL beyond generation timestamp')
    units = find_adders(source)
    design = json.loads(args.netlist.read_text())
    units, cell_count = check_chains(design['modules']['mkTop'], units)
    report = {
        'status': 'pass', 'affine_accumulation_adders': len(units), 'width': 24,
        'description': 'Live distinct 24-bit accumulation circuits; normal and final-product rules share one circuit per engine.',
        'scope': 'Supplied whole-baseline netlist, not an inferred result for another mapping or implementation.',
        'mapped_netlist_mapping': args.mapping_label, 'mapped_netlist_creator': design['creator'],
        'current_rtl_equals_mapped_input_except_generation_timestamp': True,
        'state_retained': True, 'total_distinct_carry_cells': cell_count,
        'excluded': ['normalization sums', 'bias/alignment adders', 'convolution accumulators',
                     'scan state arithmetic', 'counter/address incrementers'],
        'sources': [{'role': role, 'path': str(path), 'sha256': digest(path)} for role, path in
                    [('current_rtl', args.rtl), ('mapped_input_rtl', mapped_rtl), ('netlist', args.netlist)]],
        'warning': 'Carry-cell inventory demonstrates live circuits, not a projected net-area saving.',
        'units': units,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'affine_accumulation_adders',
                                                 'width', 'total_distinct_carry_cells')}))


if __name__ == '__main__':
    main()
