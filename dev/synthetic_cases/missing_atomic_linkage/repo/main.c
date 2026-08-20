int main(void) { __int128 value = 0; return __atomic_compare_exchange_n(&value, &value, 1, 0, 5, 5); }
