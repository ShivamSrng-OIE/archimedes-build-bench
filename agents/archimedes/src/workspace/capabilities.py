"""Record which helper executables are on ``PATH`` without running any of them."""

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
    """The helper tools found on ``PATH`` at startup.

    Attributes:
        executables: Tool name mapped to its full path, for each name in
            ``TOOLS`` that was found.
    """

    executables: dict[str, str]

    @classmethod
    def discover(cls) -> CapabilityRegistry:
        """Look up every name in ``TOOLS`` with ``shutil.which``.

        Nothing is executed, so a hostile binary on ``PATH`` cannot run.

        Returns:
            A registry holding the tools that were found.
        """
        return cls({name: path for name in TOOLS if (path := shutil.which(name))})

    def has(self, name: str) -> bool:
        """Tell whether a tool was found.

        Args:
            name: Tool name, such as ``bb-build``.

        Returns:
            True if the tool is on ``PATH``.
        """
        return name in self.executables

    def path(self, name: str) -> str | None:
        """Return where a tool was found.

        Args:
            name: Tool name, such as ``bb-build``.

        Returns:
            The full path of the tool, or ``None`` if it was not found.
        """
        return self.executables.get(name)
