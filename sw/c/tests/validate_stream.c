#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <omp.h>

#include "../sway.h"
#include "../generated/sway_model.h"


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
	FILE *inputFile = fopen(argv[1], "rb");
	if ( inputFile == NULL || fseek(inputFile, 0, SEEK_END) != 0 ) {
		printf( "Cannot read input: %s\n", argv[1] );
		if ( inputFile != NULL ) fclose(inputFile);
		return 1;
	}
	long inputSize = ftell(inputFile);
	if ( inputSize <= 0 || inputSize % 320 != 0 || fseek(inputFile, 0, SEEK_SET) != 0 ) {
		printf( "Invalid input size\n" );
		fclose(inputFile);
		return 1;
	}
	size_t frameNum = (size_t)inputSize / 320;
	int8_t *inputs = malloc((size_t)inputSize);
	int8_t *outputs = malloc(frameNum * 57);
	if ( inputs == NULL || outputs == NULL || fread(inputs, 1, (size_t)inputSize, inputFile) != (size_t)inputSize ) {
		printf( "Input allocation/read failed\n" );
		free(inputs);
		free(outputs);
		fclose(inputFile);
		return 1;
	}
	fclose(inputFile);
	memset(outputs, 0x55, frameNum * 57);
	SwayWorkspace workspace;
	memset(&workspace, 0, sizeof(workspace));
	omp_set_dynamic(0);
	// Only the inference kernel synchronizes the persistent team between frames.
	#pragma omp parallel num_threads(coreNum) shared(workspace, inputs, outputs)
	{
		if ( omp_get_num_threads() != coreNum ) {
			printf( "OpenMP did not provide the requested core count\n" );
			exit(1);
		}
		int coreId = omp_get_thread_num();
		for ( size_t frame = 0; frame < frameNum; frame ++ ) {
			swayForwardWorker(&swayModel, &workspace, inputs + frame * 320,
				outputs + frame * 57, coreId, (int)coreNum);
		}
	}
	FILE *outputFile = fopen(argv[2], "wb");
	if ( outputFile == NULL || fwrite(outputs, 57, frameNum, outputFile) != frameNum ) {
		printf( "Cannot write stream outputs: %s\n", argv[2] );
		if ( outputFile != NULL ) fclose(outputFile);
		free(inputs);
		free(outputs);
		return 1;
	}
	int closeStatus = fclose(outputFile);
	free(inputs);
	free(outputs);
	if ( closeStatus != 0 ) {
		printf( "Output close failed\n" );
		return 1;
	}
	printf( "Persistent-team execution: %zu frames, %ld cores\n", frameNum, coreNum );
	return 0;
}
