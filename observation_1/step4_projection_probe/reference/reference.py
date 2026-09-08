#!/usr/bin/env python3
"""Integer reference and fixtures. These tests are NOT HDL simulation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import random
import unittest

FRACTION = 18
LANES = 4
TOKENS = 7
MAGNITUDE = (0, 128, 64, 16, 32, 160, 96, 48)


def signed(value: int, width: int = 32) -> int:
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def reciprocal_radix4(denominator: int) -> tuple[int, bool]:
    denominator &= 0xffffffff
    if denominator == 0:
        return 0, True
    remainder = 16
    for _ in range(16):
        if remainder >= denominator:
            remainder -= denominator
    quotient = 0
    for _ in range(16):
        trial = remainder * 4
        digit = 0
        if trial >= denominator * 3:
            digit = 3
        elif trial >= denominator * 2:
            digit = 2
        elif trial >= denominator:
            digit = 1
        remainder = trial - digit * denominator
        quotient = ((quotient << 2) | digit) & 0xffffffff
        assert 0 <= remainder < denominator
    return quotient, False


def quantize_round(raw: int) -> int:
    raw = signed(raw)
    rounded = signed(raw + (1 << 17) if raw >= 0 else raw - (1 << 17))
    if rounded >= 33423360:
        return 127
    if rounded <= -33685504:
        return -128
    integer = abs(rounded) >> FRACTION
    return signed(-integer if rounded < 0 else integer, 8)


def quantize_token(values: list[int], smoothing: list[int], staged: bool = False) -> dict:
    smoothed = [signed((signed(x) * (s & 0xffffffff)) >> 26)
                for x, s in zip(values, smoothing)]
    amax = max([0] + [signed(-x) if x < 0 else x for x in smoothed])
    scale = ((amax * 2064) >> FRACTION) & 0xffffffff
    if staged:
        inverse, zero = reciprocal_radix4(scale)
    else:
        inverse, zero = (((1 << 36) // scale) & 0xffffffff, False) if scale else (0, True)
    reciprocal_signed = signed(inverse)
    scaled = [signed((x * reciprocal_signed) >> FRACTION) for x in smoothed]
    quantized = [0 if zero else quantize_round(x) for x in scaled]
    packed = sum((x & 255) << (8*i) for i,x in enumerate(quantized))
    return dict(smoothed=smoothed, maximum=amax, scale=scale, inverse=inverse,
                zero=zero, quantized=quantized, packed=packed)


def apot_shift_add(activation: int, code: int) -> int:
    x = signed(activation, 8)
    shifts = [0, signed(x << 7, 16), signed(x << 6, 16),
              signed(x << 4, 16), signed(x << 5, 16),
              signed((x << 7)+(x << 5), 16), signed((x << 6)+(x << 5), 16),
              signed((x << 4)+(x << 5), 16)]
    value = shifts[code & 7]
    return signed(-value if code & 8 else value, 16)


def write_hex(path: Path, values: list[int], width: int) -> None:
    path.write_text(''.join(f'{x & ((1 << width)-1):0{width//4}x}\n' for x in values))


def make_fixture(directory: Path, seed: int, stalled: bool, special: bool) -> dict:
    rng = random.Random(seed)
    smoothing = [1 << 26, 3 << 24, 3 << 25, 1 << 25]
    values = [[rng.randint(-8*(1 << 18), 8*(1 << 18)) for _ in range(4)]
              for _ in range(7)]
    if special:
        values[0] = [0, 0, 0, 0]
        values[1] = [1, -1, 2, -2]
        values[2] = [1 << 18, -(1 << 18), 0, 3 << 17]
        values[3] = [-(1 << 31), (1 << 31)-1, -4000, 7000]
    weights = [[rng.randrange(16) for _ in range(4)] for _ in range(16)]
    quantized = [quantize_token(v, smoothing) for v in values]
    expected = []
    for q in quantized:
        for row in weights:
            products = [apot_shift_add(x,w) for x,w in zip(q['quantized'],row)]
            # Two registered pair sums, then one registered final reduction.
            expected.append(signed(signed(products[0]+products[1])+signed(products[2]+products[3])))
    directory.mkdir(parents=True, exist_ok=True)
    for lane in range(4):
        write_hex(directory/f'input{lane}.hex', [v[lane] for v in values], 32)
        write_hex(directory/f'weight{lane}.hex',
                  [sum(weights[tile*4+lane][i] << (4*i) for i in range(4)) for tile in range(4)], 16)
    write_hex(directory/'smooth.hex', smoothing, 32)
    write_hex(directory/'control.hex', [int(stalled), 5 if stalled else 0], 32)
    write_hex(directory/'expected.hex', expected, 32)
    metadata = dict(seed=seed, stalled=stalled, special=special, input=values,
                    smoothing=smoothing, weights=weights, expected=expected, quant=quantized,
                    scope='synthetic arithmetic unit tests; no trained model data')
    (directory/'metadata.json').write_text(json.dumps(metadata, indent=2))
    return metadata


class ReferenceTests(unittest.TestCase):
    def test_reciprocal_directed(self):
        denoms = list(range(1,257)) + [2**k-1 for k in range(9,33)] + [2**k for k in range(8,32)]
        for d in denoms:
            self.assertEqual(reciprocal_radix4(d), (((1 << 36)//d)&0xffffffff, False))
    def test_reciprocal_random(self):
        rng = random.Random(41)
        for _ in range(10000):
            d = rng.randrange(1, 1 << 32)
            self.assertEqual(reciprocal_radix4(d), (((1 << 36)//d)&0xffffffff, False))
    def test_zero_scale_guard(self):
        self.assertEqual(reciprocal_radix4(0), (0, True))
        q = quantize_token([0]*4, [1 << 26]*4)
        self.assertTrue(q['zero'])
        self.assertEqual(q['quantized'], [0]*4)
    def test_apot_exhaustive(self):
        for x in range(-128,128):
            for c in range(16):
                expected = x * MAGNITUDE[c & 7] * (-1 if c & 8 else 1)
                self.assertEqual(apot_shift_add(x,c), expected)
    def test_rounding_boundaries(self):
        for half_integer in range(-255,256):
            for delta in (-1,0,1):
                raw = half_integer*(1 << 17)+delta
                # Independent rational nearest with ties away from zero + saturation.
                magnitude = (abs(raw)+(1 << 17))//(1 << 18)
                expected = -magnitude if raw < 0 else magnitude
                self.assertEqual(quantize_round(raw), max(-128,min(127,expected)))
    def test_quantization_staged_matches_direct(self):
        rng = random.Random(43)
        for _ in range(2500):
            values = [rng.randrange(-(1 << 31),1 << 31) for _ in range(4)]
            factors = [rng.randrange(1,1 << 29) for _ in range(4)]
            self.assertEqual(quantize_token(values,factors,True), quantize_token(values,factors,False))
    def test_packing_and_projection(self):
        for seed in (7,19,31):
            meta = make_fixture(Path('/tmp/sway_reference_fixture'), seed, False, True)
            self.assertEqual(len(meta['expected']),112)
            for t,q in enumerate(meta['quant']):
                for row in range(16):
                    expected = sum(q['quantized'][i]*MAGNITUDE[meta['weights'][row][i]&7]*
                                   (-1 if meta['weights'][row][i]&8 else 1) for i in range(4))
                    self.assertEqual(meta['expected'][t*16+row], expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture-dir', type=Path)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--stalled', action='store_true')
    parser.add_argument('--special', action='store_true')
    args = parser.parse_args()
    if args.fixture_dir:
        make_fixture(args.fixture_dir,args.seed,args.stalled,args.special)
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)

if __name__ == '__main__':
    main()
