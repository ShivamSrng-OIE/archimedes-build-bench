#ifdef HAVE_OPTIMIZED_CRC
extern int optimized_crc(int);
int crc(int x) { return optimized_crc(x); }
#endif
