#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <omp.h>

#include "../sway.h"
#include "../generated/sway_model.h"


static SwayWorkspace workspaces[8];
static SwayTrace traces[8];
static int8_t inputs[8][320] __attribute__((aligned(4)));
static int8_t outputs[8][57] __attribute__((aligned(4)));


static void writeArray(FILE *file, const void *data, size_t elementSize, size_t count) {
	if ( fwrite(data, elementSize, count, file) != count ) {
		printf( "Output write failed\n" );
		exit(1);
	}
}


int main(int argc, char **argv) {
	if ( argc != 4 ) {
		printf( "Usage: %s input.bin output.bin cores\n", argv[0] );
		return 1;
	}
	char *end = NULL;
	long coreNum = strtol(argv[3], &end, 10);
	if ( end == argv[3] || *end != '\0' || coreNum < 1 || coreNum > 8 ) {
		printf( "Invalid core count\n" );
		return 1;
	}
	omp_set_dynamic(0);
	FILE *inputFile = fopen(argv[1], "rb");
	FILE *outputFile = fopen(argv[2], "wb");
	if ( inputFile == NULL || outputFile == NULL ) {
		printf( "Cannot open input/output files\n" );
		if ( inputFile != NULL ) fclose(inputFile);
		if ( outputFile != NULL ) fclose(outputFile);
		return 1;
	}
	for ( int core = 0; core < 8; core ++ ) {
		workspaces[core].trace = &traces[core];
	}
	unsigned frameNum = 0;
	size_t inputBytes;
	while ( (inputBytes = fread(inputs, 1, sizeof(inputs), inputFile)) != 0 ) {
		if ( inputBytes % 320 != 0 ) {
			printf( "Truncated input frame\n" );
			fclose(inputFile);
			fclose(outputFile);
			return 1;
		}
		int batchNum = (int)(inputBytes / 320);
		#pragma omp parallel for num_threads(coreNum) schedule(static, 1)
		for ( int frame = 0; frame < batchNum; frame ++ ) {
			// Each independent frame owns its complete recurrent state and scratch.
			swayForward(&swayModel, &workspaces[frame], inputs[frame], outputs[frame]);
		}
		for ( int frame = 0; frame < batchNum; frame ++ ) {
			writeArray(outputFile, outputs[frame], sizeof(int8_t), 57);
			writeArray(outputFile, traces[frame].embedding, sizeof(int8_t), 16 * 20);
			writeArray(outputFile, traces[frame].residual, sizeof(int8_t), 2 * 16 * 20);
			writeArray(outputFile, traces[frame].headHidden, sizeof(int8_t), 20);
			writeArray(outputFile, traces[frame].activated, sizeof(int8_t), 20);
			writeArray(outputFile, traces[frame].currentState, sizeof(int32_t), 2 * 16 * 40 * 8);
			writeArray(outputFile, traces[frame].state, sizeof(int32_t), 2 * 16 * 40 * 8);
		}
		frameNum += (unsigned)batchNum;
	}
	if ( ferror(inputFile) ) {
		printf( "Input read failed\n" );
		fclose(inputFile);
		fclose(outputFile);
		return 1;
	}
	if ( fclose(inputFile) != 0 || fclose(outputFile) != 0 ) {
		printf( "File close failed\n" );
		return 1;
	}
	printf( "Independent-frame execution: %u frames, %ld cores\n", frameNum, coreNum );
	return 0;
}
