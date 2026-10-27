#include "pmsis.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sway.h"
#include "generated/sway_model.h"
#include "generated/sway_fixtures.h"

// Pinned GAP SDK PMU driver: programmed FLL rates after 32,768 Hz rounding.
// pi_freq_get() retains the requested rates in this PULP OS version.
extern unsigned int FllsFrequency[];

#ifndef SWAY_CL_HZ
#define SWAY_CL_HZ 170000000
#endif
#ifndef SWAY_FC_HZ
#define SWAY_FC_HZ 250000000
#endif
#ifndef SWAY_REPEATS
#define SWAY_REPEATS 8
#endif
#ifndef SWAY_TRIALS
#define SWAY_TRIALS 3
#endif
#ifndef SWAY_SIMD
#define SWAY_SIMD 1
#endif
#ifndef SWAY_MAX_CORES
#define SWAY_MAX_CORES 8
#endif
#define SWAY_BENCH_FRAMES (SWAY_REAL_FRAMES * SWAY_REPEATS)
#define SWAY_OUTPUT_FRAMES (SWAY_BENCH_FRAMES > SWAY_FIXTURE_FRAMES ? SWAY_BENCH_FRAMES : SWAY_FIXTURE_FRAMES)

typedef struct {
	const SwayModel *model;
	SwayWorkspace *workspace;
	int8_t (*output)[57];
	int frames;
	int fixtureFrames;
	int cores;
	int frameParallel;
	uint32_t cycles;
	uint32_t referenceUs;
} Benchmark;

static PI_L2 int8_t benchmarkOutput[SWAY_OUTPUT_FRAMES][57];
static PI_L2 Benchmark benchmark;

// Keep one team active for the entire stream of independent radar frames.
static void inferenceWorker(void *argument) {
	Benchmark *run = (Benchmark *)argument;
	int coreId = pi_core_id();
	if ( run->frameParallel ) {
		for ( int frameIdx = coreId; frameIdx < run->frames; frameIdx += run->cores ) {
			swayForward(run->model, &run->workspace[coreId],
				swayFixtureInput[frameIdx % run->fixtureFrames], run->output[frameIdx]);
		}
	} else {
		for ( int frameIdx = 0; frameIdx < run->frames; frameIdx ++ ) {
			swayForwardWorker(run->model, run->workspace,
				swayFixtureInput[frameIdx % run->fixtureFrames], run->output[frameIdx],
				coreId, run->cores);
		}
	}
}

static void clusterEntry(void *argument) {
	Benchmark *run = (Benchmark *)argument;
	// PI_PERF_CYCLES is the shared cluster elapsed-time counter.
	pi_perf_conf(1 << PI_PERF_CYCLES);
	pi_perf_reset();
	pi_perf_start();
	pi_cl_team_fork(run->cores, inferenceWorker, run);
	pi_perf_stop();
	run->cycles = pi_perf_read(PI_PERF_CYCLES);
}

static uint32_t runCluster(struct pi_device *cluster, Benchmark *run) {
	struct pi_cluster_task task;
	pi_cluster_task(&task, clusterEntry, run);
	task.stack_size = 2048;
	task.slave_stack_size = 1024;
	uint32_t startUs = pi_time_get_us();
	pi_perf_conf(1 << PI_PERF_CYCLES);
	pi_perf_reset();
	pi_perf_start();
	int status = pi_cluster_send_task_to_cl(cluster, &task);
	pi_perf_stop();
	run->referenceUs = pi_time_get_us() - startUs;
	if ( status ) {
		printf( "SWAY_ERROR cluster_dispatch status=%d\n", status );
		pmsis_exit(1);
	}
	return pi_perf_read(PI_PERF_CYCLES);
}

static int verifyOutput(int frames, int fixtureFrames) {
	for ( int frameIdx = 0; frameIdx < frames; frameIdx ++ ) {
		for ( int outputIdx = 0; outputIdx < 57; outputIdx ++ ) {
			int actual = benchmarkOutput[frameIdx][outputIdx];
			int expected = swayFixtureExpected[frameIdx % fixtureFrames][outputIdx];
			if ( actual != expected ) {
				printf( "SWAY_ERROR frame=%d coordinate=%d actual=%d expected=%d\n",
					frameIdx, outputIdx, actual, expected );
				return 1;
			}
		}
	}
	return 0;
}

static void benchmarkMain(void) {
	struct pi_device cluster;
	struct pi_cluster_conf config;
	pi_cluster_conf_init(&config);
	config.id = 0;
	pi_open_from_conf(&cluster, &config);
	if ( pi_cluster_open(&cluster) ) {
		printf( "SWAY_ERROR cluster_open\n" );
		pmsis_exit(1);
		return;
	}
	if ( pi_freq_set(PI_FREQ_DOMAIN_FC, SWAY_FC_HZ) || pi_freq_set(PI_FREQ_DOMAIN_CL, SWAY_CL_HZ) ) {
		printf( "SWAY_ERROR clock_configuration\n" );
		pmsis_exit(1);
		return;
	}
	SwayModel *localModel = pi_cl_l1_malloc(&cluster, sizeof(SwayModel));
	if ( localModel == NULL ) {
		printf( "SWAY_ERROR model_l1_allocation bytes=%u\n", (unsigned)sizeof(SwayModel) );
		pmsis_exit(1);
		return;
	}
	// Fixed model initialization is excluded from warm inference, as in the FPGA.
	memcpy(localModel, &swayModel, sizeof(SwayModel));
	benchmark.model = localModel;
	benchmark.output = benchmarkOutput;
	printf( "[STEP 1] Frozen model loaded; inputs and results remain in L2.\n" );
	printf( "SWAY_CONFIG fc_hz=%u cl_hz=%u fc_requested_hz=%u cl_requested_hz=%u model_bytes=%u workspace_bytes=%u simd=%d frames=%d trials=%d\n",
		FllsFrequency[0], FllsFrequency[1],
		(unsigned)pi_freq_get(PI_FREQ_DOMAIN_FC), (unsigned)pi_freq_get(PI_FREQ_DOMAIN_CL),
		(unsigned)sizeof(SwayModel), (unsigned)sizeof(SwayWorkspace), SWAY_SIMD,
		SWAY_BENCH_FRAMES, SWAY_TRIALS );
	int errors = 0;
	int configurations = 0;
	for ( int frameParallel = 0; frameParallel <= 1; frameParallel ++ ) {
		for ( int cores = 1; cores <= SWAY_MAX_CORES; cores *= 2 ) {
			int workspaces = frameParallel ? cores : 1;
			unsigned workspaceBytes = sizeof(SwayWorkspace) * workspaces;
			SwayWorkspace *workspace = pi_cl_l1_malloc(&cluster, workspaceBytes);
			if ( workspace == NULL ) {
				printf( "SWAY_SKIP schedule=%s cores=%d reason=l1_capacity bytes=%u\n",
					frameParallel ? "frames" : "cooperative", cores, workspaceBytes );
				continue;
			}
			memset(workspace, 0, workspaceBytes);
			benchmark.workspace = workspace;
			benchmark.cores = cores;
			benchmark.frameParallel = frameParallel;
			benchmark.frames = SWAY_FIXTURE_FRAMES;
			benchmark.fixtureFrames = SWAY_FIXTURE_FRAMES;
			runCluster(&cluster, &benchmark);
			errors += verifyOutput(benchmark.frames, benchmark.fixtureFrames);
			printf( "SWAY_CHECK schedule=%s cores=%d frames=%d errors=%d\n",
				frameParallel ? "frames" : "cooperative", cores, benchmark.frames, errors );
			benchmark.frames = SWAY_BENCH_FRAMES;
			benchmark.fixtureFrames = SWAY_REAL_FRAMES;
			for ( int trial = 0; trial < SWAY_TRIALS && errors == 0; trial ++ ) {
				uint32_t fcCycles = runCluster(&cluster, &benchmark);
				errors += verifyOutput(benchmark.frames, benchmark.fixtureFrames);
				printf( "SWAY_RESULT schedule=%s cores=%d simd=%d trial=%d frames=%d cl_cycles=%u fc_cycles=%u reference_us=%u errors=%d\n",
					frameParallel ? "frames" : "cooperative", cores, SWAY_SIMD, trial,
					benchmark.frames, (unsigned)benchmark.cycles, (unsigned)fcCycles,
					(unsigned)benchmark.referenceUs, errors );
			}
			configurations ++;
			pi_cl_l1_free(&cluster, workspace, workspaceBytes);
		}
	}
	pi_cl_l1_free(&cluster, localModel, sizeof(SwayModel));
	pi_cluster_close(&cluster);
	printf( "[STEP 2] Benchmark and exact output verification completed.\n" );
	printf( "SWAY_DONE configurations=%d errors=%d\n", configurations, errors );
	pmsis_exit(errors ? 1 : 0);
}

int main(void) {
	return pmsis_kickoff((void *)benchmarkMain);
}
