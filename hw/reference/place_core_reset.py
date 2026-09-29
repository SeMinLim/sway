#!/usr/bin/env python3
"""Place the existing core-reset FF near the center of the ECP5 logic fabric."""

import json


CORE_RESET_CELL = "clocks_coreReset.OUT_RST_TRELLIS_FF_Q"
REPORT_PREFIX = "SWAY_RESET_PLACEMENT "


def constrain_core_reset(context):
    cell = context.cells[CORE_RESET_CELL]
    if cell.type != "TRELLIS_FF":
        raise RuntimeError("Core-reset driver is not a TRELLIS_FF")

    sites = []
    for bel in context.getBels():
        if context.isValidBelForCellType(cell.type, bel):
            location = context.getBelLocation(bel)
            sites.append((bel, location.x, location.y, location.z))
    if not sites:
        raise RuntimeError("No compatible FF sites for core-reset placement")
    min_x, max_x = min(site[1] for site in sites), max(site[1] for site in sites)
    min_y, max_y = min(site[2] for site in sites), max(site[2] for site in sites)
    center_x2, center_y2 = min_x + max_x, min_y + max_y
    available = [site for site in sites if context.checkBelAvail(site[0])]
    if not available:
        raise RuntimeError("No available FF site for core-reset placement")

    # Integer doubled coordinates give a deterministic nearest-center choice.
    def distance(site):
        bel, x, y, z = site
        return ((2 * x - center_x2) ** 2 + (2 * y - center_y2) ** 2, x, y, z, bel)

    bel, x, y, z = min(available, key=distance)
    if "BEL" in cell.attrs and cell.attrs["BEL"] != bel:
        raise RuntimeError("Core-reset FF already has a different BEL constraint")
    # The placer applies BEL at USER strength and checks complete tile legality.
    # No cell, connection, reset polarity, or clock constraint is changed here.
    cell.setAttr("BEL", bel)
    return {"cell": CORE_RESET_CELL, "cell_type": cell.type, "bel": bel,
            "location": [x, y, z], "ff_grid_bounds": [min_x, min_y, max_x, max_y],
            "center": [center_x2 / 2, center_y2 / 2], "ff_sites": len(sites),
            "available_ff_sites": len(available), "selection": "nearest-available-FF-to-grid-center"}


if "ctx" in globals():
    print(REPORT_PREFIX + json.dumps(constrain_core_reset(ctx), sort_keys=True), flush=True)
