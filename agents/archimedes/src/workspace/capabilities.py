"""Read-only discovery of optional diagnostic and official runtime tools."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

TOOLS = (
    "git", "grep", "rg", "find", "cc", "gcc", "clang", "cpp", "make",
    "cmake", "meson", "ninja", "pkg-config", "readelf", "objdump", "nm",
    "file", "rpmspec", "rpm", "python", "qemu-aarch64", "qemu-riscv64",
    "llvm-objdump", "llvm-nm", "bb-build",
)


@dataclass(frozen=True, slots=True)
class CapabilityRegistry:
    executables: dict[str, str]

    @classmethod
    def discover(cls) -> "CapabilityRegistry":
        """Record executable locations without invoking untrusted tools."""
        return cls({name: path for name in TOOLS if (path := shutil.which(name))})

    def has(self, name: str) -> bool:
        return name in self.executables

    def path(self, name: str) -> str | None:
        return self.executables.get(name)
