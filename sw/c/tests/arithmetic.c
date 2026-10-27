#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "../sway_math.h"


typedef struct {
	int32_t operation;
	int32_t argument;
	int64_t value;
	int64_t expected;
} ArithmeticCase;

_Static_assert(sizeof(ArithmeticCase) == 24, "Unexpected arithmetic fixture layout");


int main(int argc, char **argv) {
	if ( argc != 2 ) {
		printf( "Usage: %s cases.bin\n", argv[0] );
		return 1;
	}
	FILE *file = fopen(argv[1], "rb");
	if ( file == NULL ) {
		printf( "Cannot open arithmetic fixtures: %s\n", argv[1] );
		return 1;
	}
	ArithmeticCase test;
	uint64_t caseNum = 0;
	size_t size;
	while ( (size = fread(&test, 1, sizeof(test), file)) == sizeof(test) ) {
		int64_t actual;
		switch ( test.operation ) {
			case 0: actual = swayDivideEven32((int32_t)test.value, (uint32_t)test.argument); break;
			case 1: actual = swayRoundShift32((int32_t)test.value, (unsigned)test.argument); break;
			case 2: actual = swayRoundShift64(test.value, (unsigned)test.argument); break;
			case 3: actual = swayRequantize32((int32_t)test.value, test.argument); break;
			case 4: actual = swayRequantize64(test.value, test.argument); break;
			case 5: actual = swaySaturate24((int32_t)test.value); break;
			case 6: actual = swayRetainState((int32_t)test.value); break;
			case 7:
				actual = swayDivideEvenReciprocal32((int32_t)test.value, (uint32_t)test.argument,
					(uint32_t)(UINT64_C(4294967296) / (uint32_t)test.argument));
				break;
			case 8: actual = swayRequantizeBounded32((int32_t)test.value, test.argument); break;
			default:
				printf( "Unknown arithmetic test operation\n" );
				fclose(file);
				return 1;
		}
		if ( actual != test.expected ) {
			printf( "Arithmetic mismatch case=%" PRIu64 " operation=%d value=%" PRId64
				" argument=%d expected=%" PRId64 " actual=%" PRId64 "\n",
				caseNum, test.operation, test.value, test.argument, test.expected, actual );
			fclose(file);
			return 1;
		}
		caseNum ++;
	}
	if ( size != 0 || ferror(file) ) {
		printf( "Truncated or unreadable arithmetic fixtures\n" );
		fclose(file);
		return 1;
	}
	fclose(file);
	printf( "Arithmetic validation passed: %" PRIu64 " cases\n", caseNum );
	return 0;
}
