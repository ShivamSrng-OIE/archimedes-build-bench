#include <immintrin.h>
int vector_sum(void) { return _mm256_extract_epi32(_mm256_setzero_si256(), 0); }
