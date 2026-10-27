#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _OPENMP
#include <omp.h>
#endif

#include "../sway.h"
#include "../generated/sway_model.h"


// Read and write one complete tensor.
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
		printf( "Invalid core count: %s\n", argv[3] );
		return 1;
	}
#ifndef _OPENMP
	if ( coreNum != 1 ) {
		printf( "Multicore validation requires OpenMP\n" );
		return 1;
	}
#else
	omp_set_dynamic(0);
#endif
	FILE *inputFile = fopen(argv[1], "rb");
	FILE *outputFile = fopen(argv[2], "wb");
	if ( inputFile == NULL || outputFile == NULL ) {
		printf( "Cannot open input/output files\n" );
		if ( inputFile != NULL ) fclose(inputFile);
		if ( outputFile != NULL ) fclose(outputFile);
		return 1;
	}
	SwayWorkspace workspace;
	memset(&workspace, 0, sizeof(workspace));
#ifdef SWAY_TRACE
	SwayTrace trace;
	memset(&trace, 0, sizeof(trace));
	workspace.trace = &trace;
#endif
	int8_t input[320] __attribute__((aligned(4)));
	int8_t output[57] __attribute__((aligned(4)));
	unsigned int frameNum = 0;
	size_t inputSize;
	while ( (inputSize = fread(input, 1, sizeof(input), inputFile)) == sizeof(input) ) {
		memset(output, 0x55, sizeof(output));
#ifdef _OPENMP
		#pragma omp parallel num_threads(coreNum) shared(workspace, input, output)
		{
			if ( omp_get_num_threads() != coreNum ) {
				printf( "OpenMP did not provide the requested core count\n" );
				exit(1);
			}
			swayForwardWorker(&swayModel, &workspace, input, output, omp_get_thread_num(), (int)coreNum);
		}
#else
		swayForward(&swayModel, &workspace, input, output);
#endif
		writeArray(outputFile, output, sizeof(int8_t), 57);
#ifdef SWAY_TRACE
		writeArray(outputFile, trace.embedding, sizeof(int8_t), 16 * 20);
		writeArray(outputFile, trace.residual, sizeof(int8_t), 2 * 16 * 20);
		writeArray(outputFile, trace.headHidden, sizeof(int8_t), 20);
		writeArray(outputFile, trace.activated, sizeof(int8_t), 20);
		writeArray(outputFile, trace.currentState, sizeof(int32_t), 2 * 16 * 40 * 8);
		writeArray(outputFile, trace.state, sizeof(int32_t), 2 * 16 * 40 * 8);
#endif
		frameNum ++;
	}
	if ( inputSize != 0 || ferror(inputFile) ) {
		printf( "Truncated or unreadable input frame\n" );
		fclose(inputFile);
		fclose(outputFile);
		return 1;
	}
	if ( fclose(inputFile) != 0 || fclose(outputFile) != 0 ) {
		printf( "File close failed\n" );
		return 1;
	}
	printf( "Validated execution: %u frames, %ld cores\n", frameNum, coreNum );
	return 0;
}
