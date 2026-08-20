#include <x86intrin.h>
unsigned crc(unsigned state, unsigned long value) { return _mm_crc32_u64(state, value); }
