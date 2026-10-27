#ifndef SWAY_MATH_H
#define SWAY_MATH_H

#include <stdint.h>

#if (-1 >> 1) != -1
#error "Sway requires the arithmetic signed right shift provided by GCC/Clang and GAP8"
#endif

static inline int32_t swayClip8(int32_t value) {
#ifdef SWAY_GAP8
	return __builtin_pulp_clip(value, -128, 127);
#else
	return value > 127 ? 127 : value < -128 ? -128 : value;
#endif
}

// Unsigned magnitudes avoid signed overflow for the minimum signed integer.
static inline int32_t swaySigned32(uint32_t magnitude, int negative) {
	if ( negative && magnitude != 0 ) {
		return -(int32_t)(magnitude - 1) - 1;
	}
	return (int32_t)magnitude;
}

static inline int64_t swaySigned64(uint64_t magnitude, int negative) {
	if ( negative && magnitude != 0 ) {
		return -(int64_t)(magnitude - 1) - 1;
	}
	return (int64_t)magnitude;
}

static inline int32_t swayDivideEven32(int32_t numerator, uint32_t denominator) {
	uint32_t magnitude = numerator < 0 ? 0U - (uint32_t)numerator : (uint32_t)numerator;
	uint32_t quotient = magnitude / denominator;
	uint32_t remainder = magnitude - quotient * denominator;
	quotient += remainder > denominator - remainder ||
		(remainder == denominator - remainder && (quotient & 1U));
	return swaySigned32(quotient, numerator < 0);
}

// reciprocal is floor(2^32 / denominator), with denominator >= 2.
static inline int32_t swayDivideEvenReciprocal32(int32_t numerator,
	uint32_t denominator, uint32_t reciprocal) {
	uint32_t magnitude = numerator < 0 ? 0U - (uint32_t)numerator : (uint32_t)numerator;
	uint32_t quotient = ((uint64_t)magnitude * reciprocal) >> 32;
	uint32_t remainder = magnitude - quotient * denominator;
	uint32_t correction = remainder >= denominator;
	quotient += correction;
	// The reciprocal can underestimate floor division by at most one.
	remainder -= correction * denominator;
	quotient += ((remainder > denominator - remainder) |
		((remainder == denominator - remainder) & (quotient & 1U)));
	return swaySigned32(quotient, numerator < 0);
}

static inline int32_t swayRoundShift32(int32_t value, unsigned shift) {
	if ( shift == 0 ) {
		return value;
	}
	if ( shift >= 32 ) {
		return 0;
	}
	int32_t quotient = value >> shift;
	uint32_t remainder = (uint32_t)value & ((1U << shift) - 1U);
	uint32_t half = 1U << (shift - 1);
	return quotient + (int32_t)((remainder > half) | ((remainder == half) & ((uint32_t)quotient & 1U)));
}

static inline int64_t swayRoundShift64(int64_t value, unsigned shift) {
	if ( shift == 0 ) {
		return value;
	}
	if ( shift >= 64 ) {
		return 0;
	}
	int64_t quotient = value >> shift;
	uint64_t remainder = (uint64_t)value & ((UINT64_C(1) << shift) - 1);
	uint64_t half = UINT64_C(1) << (shift - 1);
	return quotient + (int64_t)((remainder > half) | ((remainder == half) & ((uint64_t)quotient & 1U)));
}

static inline int8_t swayRequantize32(int32_t value, int shift) {
	if ( shift > 0 ) {
		if ( shift >= 8 ) {
			return value == 0 ? 0 : value < 0 ? -128 : 127;
		}
		// Clipping before multiplication bounds the product without changing the final saturation.
		return (int8_t)swayClip8(swayClip8(value) * (1 << shift));
	}
	value = swayRoundShift32(value, (unsigned)-shift);
	return (int8_t)swayClip8(value);
}

static inline int8_t swayRequantize64(int64_t value, int shift) {
	if ( shift > 0 ) {
		if ( shift >= 8 ) {
			return value == 0 ? 0 : value < 0 ? -128 : 127;
		}
		if ( value > (127 >> shift) ) {
			return 127;
		}
		if ( value < -(128 >> shift) ) {
			return -128;
		}
		return (int8_t)(value * (1 << shift));
	}
	value = swayRoundShift64(value, (unsigned)-shift);
	return value > 127 ? 127 : value < -128 ? -128 : (int8_t)value;
}

// The fixed model has |value| < 2^30 and right shifts below 30 at these call sites.
static inline int8_t swayRequantizeBounded32(int32_t value, int shift) {
	if ( shift < 0 ) {
		int right = -shift;
		int bias = (1 << (right - 1)) - 1 + ((value >> right) & 1);
		return (int8_t)swayClip8((value + bias) >> right);
	}
	return swayRequantize32(value, shift);
}

static inline int32_t swaySaturate24(int32_t value) {
#ifdef SWAY_GAP8
	return __builtin_pulp_clip(value, -8388608, 8388607);
#else
	return value > 8388607 ? 8388607 : value < -8388608 ? -8388608 : value;
#endif
}

static inline int32_t swayRetainState(int32_t current) {
	// The frozen contract uses floor division, not nearest rounding, here.
	return current >> 7;
}

#endif
