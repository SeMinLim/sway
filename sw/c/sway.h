#ifndef SWAY_H
#define SWAY_H

#include <stdint.h>

#define SWAY_INPUT_SIZE 320
#define SWAY_OUTPUT_SIZE 57
#define SWAY_TOKENS 16
#define SWAY_WIDTH 20
#define SWAY_CHANNELS 40
#define SWAY_STATE_SIZE 8
#define SWAY_ALIGN __attribute__((aligned(4)))

typedef struct {
	int8_t normWeight[20] SWAY_ALIGN;
	int8_t normBias[20] SWAY_ALIGN;
	int8_t inWeight[80][20] SWAY_ALIGN;
	int8_t convWeight[40][4] SWAY_ALIGN;
	int8_t convBias[40] SWAY_ALIGN;
	int8_t xWeight[18][40] SWAY_ALIGN;
	int8_t dtWeight[40][2] SWAY_ALIGN;
	int8_t dtBias[40] SWAY_ALIGN;
	int8_t aWeight[40][8] SWAY_ALIGN;
	int8_t dWeight[40] SWAY_ALIGN;
	int8_t outWeight[20][40] SWAY_ALIGN;
} SwayBlock;

typedef struct {
	int8_t embeddingWeight[20][20] SWAY_ALIGN;
	int8_t embeddingBias[20] SWAY_ALIGN;
	SwayBlock blocks[2];
	int8_t headWeight[20][320] SWAY_ALIGN;
	int8_t headBias[20] SWAY_ALIGN;
	int8_t outputWeight[57][20] SWAY_ALIGN;
	int8_t outputBias[60] SWAY_ALIGN;
	int8_t tables[4][256] SWAY_ALIGN;
	int8_t expProduct0[2048] SWAY_ALIGN;
	int8_t expProduct1[512] SWAY_ALIGN;
	int8_t gateInputTable[2][256] SWAY_ALIGN;
	uint32_t normReciprocal[256] SWAY_ALIGN;
} SwayModel;

// Optional host validation trace. Production inference does not allocate it.
typedef struct {
	int32_t currentState[2][16][40][8];
	int32_t state[2][16][40][8];
	int8_t embedding[16][20];
	int8_t residual[2][16][20];
	int8_t headHidden[20];
	int8_t activated[20];
} SwayTrace;

typedef struct {
	int8_t value[16][20] SWAY_ALIGN;
	int8_t expanded[16][80] SWAY_ALIGN;
	int8_t x[16][40] SWAY_ALIGN;
	int8_t delta[16][40] SWAY_ALIGN;
	int8_t b[16][8] SWAY_ALIGN;
	int8_t c[16][8] SWAY_ALIGN;
	int8_t gated[16][40] SWAY_ALIGN;
	int8_t head[20] SWAY_ALIGN;
	SwayTrace *trace;
} SwayWorkspace;

// Each cooperative worker uses the same workspace and contiguous core IDs.
// Initialize the workspace to zero once; every inference resets recurrent state.
void swayForwardWorker(const SwayModel *model, SwayWorkspace *workspace,
	const int8_t input[320], int8_t output[57], int coreId, int numCores);
void swayForward(const SwayModel *model, SwayWorkspace *workspace,
	const int8_t input[320], int8_t output[57]);

#endif
