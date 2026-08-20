#if defined(__x86_64__)
int platform_value(void) { return 64; }
#else
#error unsupported architecture selection
#endif
