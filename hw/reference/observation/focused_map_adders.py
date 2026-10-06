#!/usr/bin/env python3
"""Reproduce the focused noabc9 mapping of the exact affine accumulation cones."""
import argparse
import collections
import json
from pathlib import Path
import shutil
import subprocess

from inventory_adders import check_chains, digest, find_adders, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True, help='Whole-netlist report from inventory_adders.py')
    parser.add_argument('--yosys', default='yosys')
    parser.add_argument('--outputdir', type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    require(inventory['status'] == 'pass' and inventory['affine_accumulation_adders'] == 17,
            'Expected a passing 17-adder whole-netlist inventory')
    units = find_adders(args.rtl.read_text())
    require([(u['stage'], u['result_wire'], u['rtl_expression']) for u in units] ==
            [(u['stage'], u['result_wire'], u['rtl_expression']) for u in inventory['units']],
            'RTL accumulation expressions do not match the whole-netlist inventory')
    require(any(item['sha256'] == digest(args.rtl) for item in inventory['sources']),
            'RTL hash does not match the inventory source evidence')
    out = args.outputdir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    ports = []
    statements = []
    for unit in units:
        stage, alias = unit['stage'], unit['product_alias']
        ports += ['input [23:0] ' + stage + '_sumR',
                  'input [16:0] ' + stage + '_productQ_D_OUT',
                  'output [23:0] ' + unit['result_wire']]
        statements += ['wire [15:0] ' + alias + ';',
                       'assign ' + alias + ' = ' + stage + '_productQ_D_OUT[16:1];',
                       'assign ' + unit['result_wire'] + ' = ' + unit['rtl_expression'] + ';']
    (out / 'affine_adders.v').write_text(
        'module affine_adders(\n' + ',\n'.join(ports) + '\n);\n' +
        '\n'.join(statements) + '\nendmodule\n')
    yosys = shutil.which(args.yosys)
    require(yosys is not None, f'Yosys executable not found: {args.yosys}')
    command = [str(Path(yosys).resolve()), '-Q', '-T', '-p',
               'read_verilog affine_adders.v; synth_ecp5 -top affine_adders -noabc9 -json affine_adders.json; stat']
    with (out / 'focused-yosys.log').open('w') as log:
        subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True)
    design = json.loads((out / 'affine_adders.json').read_text())
    module = design['modules']['affine_adders']
    mapped_units, cell_count = check_chains(module, units, require_state=False)
    report = {
        'status': 'pass', 'adders': len(mapped_units), 'width': 24,
        'creator': design['creator'], 'command': command,
        'scope': 'Focused arithmetic cones with independent top-level operands; not a whole-baseline synthesis or area measurement.',
        'independent_top_level_operands': True, 'keep_or_preserve_attributes': False,
        'source_expressions_copied_verbatim': True,
        'actual_full_netlist_inventory': str(args.inventory),
        'actual_full_netlist_inventory_sha256': digest(args.inventory),
        'total_distinct_carry_cells': cell_count,
        'cell_types': dict(collections.Counter(cell['type'] for cell in module['cells'].values())),
        'sources': [{'path': str(path), 'sha256': digest(path)} for path in
                    [args.rtl, args.inventory, out / 'affine_adders.v', out / 'affine_adders.json',
                     out / 'focused-yosys.log']],
        'units': mapped_units,
    }
    (out / 'focused-mapping.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'adders', 'width', 'cell_types')}))


if __name__ == '__main__':
    main()
