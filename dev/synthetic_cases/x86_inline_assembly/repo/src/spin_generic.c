void spin_hint(void) { __atomic_signal_fence(__ATOMIC_SEQ_CST); }
