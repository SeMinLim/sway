#include "sway.h"
#include "sway_math.h"
#include "generated/sway_model.h"

#ifdef SWAY_GAP8
#include "pmsis.h"
#endif

#ifndef SWAY_SIMD
#define SWAY_SIMD 1
#endif

#define SWAY_MIN(a, b) ((a) < (b) ? (a) : (b))
#define BLOCK_EXP(name) (blockIdx == 0 ? SWAY_EXP_BLOCKS_0_##name : SWAY_EXP_BLOCKS_1_##name)

static inline void swayBarrier(int numCores) {
	if ( numCores > 1 ) {
#ifdef SWAY_GAP8
		pi_cl_team_barrier();
#elif defined(_OPENMP)
#pragma omp barrier
#endif
	}
}

static inline int32_t swayDot(const int8_t *input, const int8_t *weight, int size) {
	int32_t sum = 0;
	int i = 0;
#if defined(SWAY_GAP8) && SWAY_SIMD
	typedef signed char SwayVec4 __attribute__((vector_size(4)));
	for ( ; i + 4 <= size; i += 4 ) {
		SwayVec4 inputVec;
		SwayVec4 weightVec;
		__builtin_memcpy(&inputVec, input + i, 4);
		__builtin_memcpy(&weightVec, weight + i, 4);
		sum = __builtin_pulp_sdotsp4(inputVec, weightVec, sum);
	}
#endif
	for ( ; i < size; i ++ ) {
		sum += (int32_t)input[i] * weight[i];
	}
	return sum;
}

// All affine products and aligned biases fit signed 32 bits for this model.
static inline int8_t swayAffine(const int8_t *input, const int8_t *weight,
	int size, int bias, int inputExponent, int weightExponent,
	int biasExponent, int outputExponent) {
	int accumulatorExponent = inputExponent + weightExponent;
	int common = SWAY_MIN(accumulatorExponent, biasExponent);
	int32_t sum = swayDot(input, weight, size);
	sum = sum * (1 << (accumulatorExponent - common)) + bias * (1 << (biasExponent - common));
	return swayRequantizeBounded32(sum, common - outputExponent);
}

static inline void swayNormalize(const SwayModel *model, const SwayBlock *block, const int8_t *input,
	int8_t normalized[20], int blockIdx) {
	int minimum = input[0];
	int maximum = input[0];
	int sum = 0;
	for ( int channel = 0; channel < 20; channel ++ ) {
		int value = input[channel];
		minimum = value < minimum ? value : minimum;
		maximum = value > maximum ? value : maximum;
		sum += value;
	}
	int span = maximum - minimum;
	int denominator = (span > 0 ? span : 1) * 20;
	int common = SWAY_MIN(BLOCK_EXP(NORMWEIGHT), SWAY_MIN(BLOCK_EXP(NORMBIAS), BLOCK_EXP(NORM)));
	int gammaScale = 1 << (BLOCK_EXP(NORMWEIGHT) - common);
	int betaScale = 1 << (BLOCK_EXP(NORMBIAS) - common);
	uint32_t divisor = (uint32_t)denominator << (BLOCK_EXP(NORM) - common);
	uint32_t reciprocal = model->normReciprocal[span] >> (BLOCK_EXP(NORM) - common);
	for ( int channel = 0; channel < 20; channel ++ ) {
		int centered = 20 * input[channel] - sum;
		int numerator = centered * block->normWeight[channel] * gammaScale +
			denominator * block->normBias[channel] * betaScale;
		int rounded = swayDivideEvenReciprocal32(numerator, divisor, reciprocal);
		normalized[channel] = (int8_t)swayClip8(rounded);
	}
}

// Fixed block IDs specialize shifts at compile time without changing parameters.
static inline __attribute__((always_inline)) void swayRunBlock(const SwayModel *model,
	SwayWorkspace *workspace, int blockIdx, int coreId, int numCores) {
	const SwayBlock *block = &model->blocks[blockIdx];

	// Normalize and expand independent tokens. No intermediate team barrier.
	for ( int token = coreId; token < 16; token += numCores ) {
		int8_t normalized[20] SWAY_ALIGN;
		swayNormalize(model, block, workspace->value[token], normalized, blockIdx);
		for ( int row = 0; row < 80; row ++ ) {
			workspace->expanded[token][row] = swayRequantizeBounded32(
				swayDot(normalized, block->inWeight[row], 20),
				BLOCK_EXP(NORM) + BLOCK_EXP(INWEIGHT) - BLOCK_EXP(IN));
		}
	}
	swayBarrier(numCores);

	// Causal convolution, x projection, and delta projection share token ownership.
	for ( int token = coreId; token < 16; token += numCores ) {
		for ( int channel = 0; channel < 40; channel ++ ) {
			int accumulatorExponent = BLOCK_EXP(CONVINPUT) + BLOCK_EXP(CONVWEIGHT);
			int common = SWAY_MIN(accumulatorExponent, BLOCK_EXP(CONVBIAS));
			int sum = 0;
			for ( int tap = 0; tap < 4; tap ++ ) {
				int source = token + tap - 3;
				if ( source >= 0 ) {
					int value = swayRequantizeBounded32(workspace->expanded[source][channel],
						BLOCK_EXP(IN) - BLOCK_EXP(CONVINPUT));
					sum += value * block->convWeight[channel][tap];
				}
			}
			sum = sum * (1 << (accumulatorExponent - common)) +
				block->convBias[channel] * (1 << (BLOCK_EXP(CONVBIAS) - common));
			int convolved = swayRequantizeBounded32(sum, common - BLOCK_EXP(CONV));
			workspace->x[token][channel] = swayRequantizeBounded32(convolved, BLOCK_EXP(CONV) - BLOCK_EXP(X));
		}
		int8_t projected[18];
		int8_t deltaInput[2];
		for ( int row = 0; row < 18; row ++ ) {
			projected[row] = swayRequantizeBounded32(swayDot(workspace->x[token], block->xWeight[row], 40),
				BLOCK_EXP(X) + BLOCK_EXP(XWEIGHT) - BLOCK_EXP(XPROJECTION));
		}
		for ( int channel = 0; channel < 2; channel ++ ) {
			int value = projected[channel] > 0 ? projected[channel] : 0;
			deltaInput[channel] = swayRequantizeBounded32(value, BLOCK_EXP(XPROJECTION) - BLOCK_EXP(DELTAINPUT));
		}
		for ( int state = 0; state < 8; state ++ ) {
			workspace->b[token][state] = swayRequantizeBounded32(projected[2 + state], BLOCK_EXP(XPROJECTION) - BLOCK_EXP(B));
			workspace->c[token][state] = swayRequantizeBounded32(projected[10 + state], BLOCK_EXP(XPROJECTION) - BLOCK_EXP(C));
		}
		for ( int channel = 0; channel < 40; channel ++ ) {
			int projectedDelta = swayAffine(deltaInput, block->dtWeight[channel], 2,
				block->dtBias[channel], BLOCK_EXP(DELTAINPUT), BLOCK_EXP(DTWEIGHT),
				BLOCK_EXP(DTBIAS), BLOCK_EXP(DELTAPROJECTION));
			workspace->delta[token][channel] = swayRequantizeBounded32(projectedDelta,
				BLOCK_EXP(DELTAPROJECTION) - BLOCK_EXP(DELTA));
		}
	}
	swayBarrier(numCores);

	// A worker retains one channel's eight states across all sixteen tokens.
	// The 64-bit reduction is necessary: eight INT24 by INT8 products can overflow INT32.
	for ( int channel = coreId; channel < 40; channel += numCores ) {
		int32_t state[8] = {0};
		for ( int token = 0; token < 16; token ++ ) {
			int delta = workspace->delta[token][channel];
			int x = workspace->x[token][channel];
			int64_t stateOutput = 0;
			for ( int stateIdx = 0; stateIdx < 8; stateIdx ++ ) {
				int product = delta * block->aWeight[channel][stateIdx];
				int limit = blockIdx == 0 ? 1024 : 256;
#ifdef SWAY_GAP8
				product = blockIdx == 0 ? __builtin_pulp_clip(product, -1024, 1023) :
					__builtin_pulp_clip(product, -256, 255);
#else
				product = product < -limit ? -limit : product >= limit ? limit - 1 : product;
#endif
				int abar = blockIdx == 0 ? model->expProduct0[product + limit] :
					model->expProduct1[product + limit];
				int bbar = swayRequantizeBounded32(delta * workspace->b[token][stateIdx],
					BLOCK_EXP(DELTA) + BLOCK_EXP(B) - BLOCK_EXP(BBAR));
				int drive = bbar * x * (1 << (BLOCK_EXP(BBAR) + BLOCK_EXP(X) - BLOCK_EXP(CURRENTSTATE)));
				int32_t current = swaySaturate24(abar * state[stateIdx] + drive);
				stateOutput += (int32_t)(current * workspace->c[token][stateIdx]);
				state[stateIdx] = swayRetainState(current);
#ifdef SWAY_TRACE
				if ( workspace->trace != 0 ) {
					workspace->trace->currentState[blockIdx][token][channel][stateIdx] = current;
					workspace->trace->state[blockIdx][token][channel][stateIdx] = state[stateIdx];
				}
#endif
			}
			int stateExponent = BLOCK_EXP(CURRENTSTATE) + BLOCK_EXP(C);
			int directExponent = BLOCK_EXP(X) + BLOCK_EXP(D);
			int common = SWAY_MIN(stateExponent, directExponent);
			int64_t sum = stateOutput * (INT64_C(1) << (stateExponent - common)) +
				(int64_t)(x * block->dWeight[channel]) * (INT64_C(1) << (directExponent - common));
			int y = swayRequantize64(sum, common - BLOCK_EXP(SSMY));
			int gate = model->gateInputTable[blockIdx][workspace->expanded[token][40 + channel] + 128];
			workspace->gated[token][channel] = swayRequantizeBounded32(y * gate,
				BLOCK_EXP(SSMY) + BLOCK_EXP(GATE) - BLOCK_EXP(GATED));
		}
	}
	swayBarrier(numCores);

	// Projection and residual addition overwrite only the token owned by this worker.
	for ( int token = coreId; token < 16; token += numCores ) {
		for ( int row = 0; row < 20; row ++ ) {
			int projected = swayRequantizeBounded32(swayDot(workspace->gated[token], block->outWeight[row], 40),
				BLOCK_EXP(GATED) + BLOCK_EXP(OUTWEIGHT) - BLOCK_EXP(OUT));
			int inputExponent = blockIdx == 0 ? SWAY_EXP_EMBEDDING : SWAY_EXP_BLOCKS_0_RESIDUAL;
			int common = SWAY_MIN(inputExponent, BLOCK_EXP(OUT));
			int sum = workspace->value[token][row] * (1 << (inputExponent - common)) +
				projected * (1 << (BLOCK_EXP(OUT) - common));
			workspace->value[token][row] = swayRequantizeBounded32(sum, common - BLOCK_EXP(RESIDUAL));
#ifdef SWAY_TRACE
			if ( workspace->trace != 0 ) {
				workspace->trace->residual[blockIdx][token][row] = workspace->value[token][row];
			}
#endif
		}
	}
	swayBarrier(numCores);
}

void swayForwardWorker(const SwayModel *model, SwayWorkspace *workspace,
	const int8_t input[320], int8_t output[57], int coreId, int numCores) {
	// Patch extraction and embedding preserve the raster and patch-channel ordering.
	for ( int token = coreId; token < 16; token += numCores ) {
		int8_t patch[20] SWAY_ALIGN;
		int patchRow = token / 4;
		int patchColumn = token % 4;
		for ( int row = 0; row < 2; row ++ ) {
			for ( int column = 0; column < 2; column ++ ) {
				for ( int channel = 0; channel < 5; channel ++ ) {
					int source = (((2 * patchRow + row) * 8) + 2 * patchColumn + column) * 5 + channel;
					patch[(row * 2 + column) * 5 + channel] = swayRequantizeBounded32(input[source],
						SWAY_EXP_INPUT - SWAY_EXP_PATCHES);
				}
			}
		}
		for ( int row = 0; row < 20; row ++ ) {
			workspace->value[token][row] = swayAffine(patch, model->embeddingWeight[row], 20,
				model->embeddingBias[row], SWAY_EXP_PATCHES, SWAY_EXP_EMBEDDING_WEIGHT,
				SWAY_EXP_EMBEDDING_BIAS, SWAY_EXP_EMBEDDING);
#ifdef SWAY_TRACE
			if ( workspace->trace != 0 ) {
				workspace->trace->embedding[token][row] = workspace->value[token][row];
			}
#endif
		}
	}
	swayBarrier(numCores);
	swayRunBlock(model, workspace, 0, coreId, numCores);
	swayRunBlock(model, workspace, 1, coreId, numCores);

	// The fixed frozen contract has identical residual and head-input exponents.
#if SWAY_EXP_BLOCKS_1_RESIDUAL != SWAY_EXP_HEADINPUT
#error "The flattened head input needs a separate requantization buffer"
#endif
	for ( int row = coreId; row < 20; row += numCores ) {
		int hidden = swayAffine(&workspace->value[0][0], model->headWeight[row], 320,
			model->headBias[row], SWAY_EXP_HEADINPUT, SWAY_EXP_HEADHIDDEN_WEIGHT,
			SWAY_EXP_HEADHIDDEN_BIAS, SWAY_EXP_HEADHIDDEN);
		workspace->head[row] = swayRequantizeBounded32(hidden > 0 ? hidden : 0,
			SWAY_EXP_HEADHIDDEN - SWAY_EXP_HEADACTIVATION);
#ifdef SWAY_TRACE
		if ( workspace->trace != 0 ) {
			workspace->trace->headHidden[row] = (int8_t)hidden;
			workspace->trace->activated[row] = workspace->head[row];
		}
#endif
	}
	swayBarrier(numCores);
	for ( int row = coreId; row < 57; row += numCores ) {
		output[row] = swayAffine(workspace->head, model->outputWeight[row], 20,
			model->outputBias[row], SWAY_EXP_HEADACTIVATION, SWAY_EXP_HEADOUTPUT_WEIGHT,
			SWAY_EXP_HEADOUTPUT_BIAS, SWAY_EXP_OUTPUT);
	}
	swayBarrier(numCores);
}

void swayForward(const SwayModel *model, SwayWorkspace *workspace,
	const int8_t input[320], int8_t output[57]) {
	swayForwardWorker(model, workspace, input, output, 0, 1);
}
