from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.reasoning.controller import RepairController
from src.schemas import RepairFamily
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASES = ROOT / "dev" / "synthetic_cases"


class SyntheticRepairTests(unittest.TestCase):
    def test_autoconf_removes_only_x86_flags_rejected_by_arm_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "generic-autoconf-x86-flags",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            log = (
                "gcc: error: unrecognized command-line option '-msse'\n"
                "gcc: error: unrecognized command-line option '-msse2'\n"
                "gcc: error: unrecognized command-line option '-mfpmath=sse'\n"
            )
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            configure = repo / "configure.ac"
            configure.write_text(
                'PLUGIN_CFLAGS="-g \\\n'
                "        -msse -msse2 -mfpmath=sse -ffast-math -O3 \\\n"
                '        -fPIC"\n',
                encoding="utf-8",
            )
            generated = repo / "configure"
            generated.write_text(
                "generated -msse -msse2 -mfpmath=sse\n", encoding="utf-8"
            )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(
                outcome.metrics.selected_repair_family,
                RepairFamily.PORTABLE_FALLBACK.value,
            )
            self.assertEqual(outcome.result.modified_paths, ["configure.ac"])
            repaired = configure.read_text(encoding="utf-8")
            self.assertNotIn("-msse", repaired)
            self.assertNotIn("-mfpmath=sse", repaired)
            self.assertIn("\n        -ffast-math -O3", repaired)
            self.assertIn("-msse", generated.read_text(encoding="utf-8"))

    def test_autoconf_rejected_x86_flags_require_one_causal_source_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "ambiguous-autoconf-x86-flags",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log="gcc: error: unrecognized command-line option '-msse'\n",
            )
            configure = repo / "configure.ac"
            original = "FIRST_CFLAGS=-msse\nSECOND_CFLAGS=-msse\n"
            configure.write_text(original, encoding="utf-8")

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(configure.read_text(encoding="utf-8"), original)

    def test_debian_multiarch_path_uses_target_triplet_from_dpkg_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "generic-multiarch-path",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            log = (
                "chmod: cannot access "
                "'debian/pkg/usr/lib/x86_64-linux-gnu/nodejs/tool.js': "
                "No such file or directory\n"
                "dpkg-buildpackage: error: debian/rules binary-arch subprocess returned exit status 2\n"
            )
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            (repo / "debian").mkdir()
            rules = repo / "debian" / "rules"
            rules.write_text(
                "#!/usr/bin/make -f\n"
                "override_dh_fixperms:\n"
                "\tchmod +x debian/pkg/usr/lib/x86_64-linux-gnu/nodejs/tool.js\n",
                encoding="utf-8",
            )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(
                outcome.metrics.selected_repair_family,
                RepairFamily.PACKAGING_RULE.value,
            )
            self.assertIn("debian/rules", outcome.result.modified_paths)
            self.assertIn(
                "$(shell dpkg-architecture -qDEB_HOST_MULTIARCH)",
                rules.read_text(encoding="utf-8"),
            )

    def test_debian_multiarch_cmake_package_path_uses_target_triplet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "generic-cmake-multiarch-path",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            log = (
                "-DDeviceLibs_DIR=/build/lib/x86_64-linux-gnu/cmake/DeviceLibs\n"
                "CMake Error at CMakeLists.txt:108 (find_package):\n"
                '  Could not find a package configuration file provided by "DeviceLibs"\n'
            )
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            (repo / "debian").mkdir()
            rules = repo / "debian" / "rules"
            rules.write_text(
                "#!/usr/bin/make -f\n"
                "override_dh_auto_configure:\n"
                "\tdh_auto_configure -- "
                "-DDeviceLibs_DIR=$(CURDIR)/build/lib/x86_64-linux-gnu/cmake/DeviceLibs\n",
                encoding="utf-8",
            )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(
                outcome.metrics.selected_repair_family,
                RepairFamily.PACKAGING_RULE.value,
            )
            self.assertIn(
                "$(shell dpkg-architecture -qDEB_HOST_MULTIARCH)",
                rules.read_text(encoding="utf-8"),
            )

    def test_rocm_multiarch_repair_backports_complete_unsigned_digest_fix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "generic-rocm-comgr-multiarch-path",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            log = (
                "-DAMDDeviceLibs_DIR=/build/lib/x86_64-linux-gnu/cmake/AMDDeviceLibs\n"
                "CMake Error at CMakeLists.txt:108 (find_package):\n"
                '  Could not find a package configuration file provided by "AMDDeviceLibs"\n'
            )
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            (repo / "debian").mkdir()
            rules = repo / "debian" / "rules"
            rules.write_text(
                "#!/usr/bin/make -f\n"
                "override_dh_auto_configure:\n"
                "\tdh_auto_configure -- "
                "-DAMDDeviceLibs_DIR=$(CURDIR)/build/lib/x86_64-linux-gnu/cmake/AMDDeviceLibs\n",
                encoding="utf-8",
            )
            comgr = repo / "amd" / "comgr"
            (comgr / "cmake").mkdir(parents=True)
            (comgr / "src").mkdir()
            generator = comgr / "cmake" / "device-libs-id.py"
            generator.write_text(
                "    digest_uchar = hash.digest()\n"
                "    digest_char = [e if e < 128 else e-256 for e in digest_uchar]\n"
                '    digest_elts = ", ".join(map(str, digest_char))\n'
                '    print(f"static const char {args.varname}[] = '
                '{{{digest_elts}, 0}};", file=open(args.output, \'w\'))\n',
                encoding="utf-8",
            )
            cache = comgr / "src" / "comgr-cache-command.cpp"
            cache.write_text(
                "  addString(H, getDeviceLibrariesIdentifier());\n", encoding="utf-8"
            )
            source = comgr / "src" / "comgr-device-libs.cpp"
            source.write_text(
                "StringRef getDeviceLibrariesIdentifier() { return DEVICE_LIBS_ID; }\n",
                encoding="utf-8",
            )
            header = comgr / "src" / "comgr-device-libs.h"
            header.write_text(
                "llvm::StringRef getDeviceLibrariesIdentifier();\n", encoding="utf-8"
            )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(
                set(outcome.result.modified_paths),
                {
                    "debian/rules",
                    "amd/comgr/cmake/device-libs-id.py",
                    "amd/comgr/src/comgr-cache-command.cpp",
                    "amd/comgr/src/comgr-device-libs.cpp",
                    "amd/comgr/src/comgr-device-libs.h",
                },
            )
            self.assertIn("unsigned char", generator.read_text(encoding="utf-8"))
            self.assertNotIn("digest_char", generator.read_text(encoding="utf-8"))
            self.assertIn("H.update", cache.read_text(encoding="utf-8"))
            self.assertIn("ArrayRef<unsigned char>", source.read_text(encoding="utf-8"))
            self.assertIn("ArrayRef<unsigned char>", header.read_text(encoding="utf-8"))

    def test_rocm_companion_backport_fails_closed_when_source_shape_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "incomplete-rocm-comgr-source-shape",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            log = (
                "-DAMDDeviceLibs_DIR=/build/lib/x86_64-linux-gnu/cmake/AMDDeviceLibs\n"
                "CMake Error at CMakeLists.txt:108 (find_package):\n"
                '  Could not find a package configuration file provided by "AMDDeviceLibs"\n'
            )
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            (repo / "debian").mkdir()
            rules = repo / "debian" / "rules"
            rules.write_text(
                "-DAMDDeviceLibs_DIR=/build/lib/x86_64-linux-gnu/cmake/AMDDeviceLibs\n",
                encoding="utf-8",
            )
            generator = repo / "amd" / "comgr" / "cmake" / "device-libs-id.py"
            generator.parent.mkdir(parents=True)
            generator.write_text(
                "digest_char = [e if e < 128 else e-256 for e in digest_uchar]\n",
                encoding="utf-8",
            )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(outcome.result.modified_paths, ["debian/rules"])
            self.assertIn("digest_char", generator.read_text(encoding="utf-8"))

    def test_debian_multiarch_install_manifests_require_compat_13_and_log_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "generic-install-multiarch-paths",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            first = "usr/lib/x86_64-linux-gnu/plugins"
            second = "usr/lib/x86_64-linux-gnu/libdemo.so.*"
            log = (
                f'dh_install: warning: Cannot find (any matches for) "{first}"\n'
                f'dh_install: warning: Cannot find (any matches for) "{second}"\n'
                "dh_install: error: missing files, aborting\n"
            )
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            (repo / "debian").mkdir()
            (repo / "debian" / "control").write_text(
                "Source: demo\nBuild-Depends: debhelper-compat (= 13)\n",
                encoding="utf-8",
            )
            first_manifest = repo / "debian" / "demo.install"
            second_manifest = repo / "debian" / "libdemo.install"
            unobserved_manifest = repo / "debian" / "dev.install"
            first_manifest.write_text(first + "\n", encoding="utf-8")
            second_manifest.write_text(second + "\n", encoding="utf-8")
            unobserved_manifest.write_text(
                "usr/lib/x86_64-linux-gnu/libdemo.a\n", encoding="utf-8"
            )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(
                outcome.metrics.selected_repair_family,
                RepairFamily.PACKAGING_RULE.value,
            )
            self.assertEqual(
                set(outcome.result.modified_paths),
                {"debian/demo.install", "debian/libdemo.install"},
            )
            self.assertIn("${DEB_HOST_MULTIARCH}", first_manifest.read_text(encoding="utf-8"))
            self.assertIn("${DEB_HOST_MULTIARCH}", second_manifest.read_text(encoding="utf-8"))
            self.assertIn("x86_64-linux-gnu", unobserved_manifest.read_text(encoding="utf-8"))

    def test_debian_multiarch_install_manifest_is_not_rewritten_before_compat_13(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "legacy-install-multiarch-path",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            token = "usr/lib/x86_64-linux-gnu/plugins"
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log=f'dh_install: Cannot find (any matches for) "{token}"\n',
            )
            (repo / "debian").mkdir()
            (repo / "debian" / "control").write_text(
                "Source: demo\nBuild-Depends: debhelper-compat (= 12)\n",
                encoding="utf-8",
            )
            manifest = repo / "debian" / "demo.install"
            manifest.write_text(token + "\n", encoding="utf-8")

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(outcome.result.modified_paths, [])
            self.assertIn("x86_64-linux-gnu", manifest.read_text(encoding="utf-8"))

    def test_every_synthetic_case_selects_expected_safe_repair_family(self) -> None:
        case_directories = sorted(path for path in SYNTHETIC_CASES.iterdir() if path.is_dir())
        self.assertEqual(len(case_directories), 16)
        for case_directory in case_directories:
            with self.subTest(case=case_directory.name), tempfile.TemporaryDirectory() as temporary:
                metadata_text = (case_directory / "case.json").read_text(encoding="utf-8")
                metadata = json.loads(metadata_text)
                log = (case_directory / "failure.log").read_text(encoding="utf-8")
                root = Path(temporary)
                repo, _ = make_workspace(root, metadata=metadata_text, log=log)
                shutil.copytree(case_directory / "repo", repo, dirs_exist_ok=True)
                outcome = RepairController(
                    config=runtime_config(root),
                    workspace=WorkspacePaths.resolve(root),
                    build_feedback=UnavailableBuildFeedbackProvider(),
                ).run()
                self.assertEqual(
                    outcome.metrics.selected_repair_family,
                    metadata["expected_repair_family"],
                )
                if metadata["expected_repair_family"] == RepairFamily.NO_SAFE_FIX.value:
                    self.assertEqual(outcome.result.modified_paths, [])
                else:
                    self.assertIn(metadata["expected_causal_file"], outcome.result.modified_paths)

    def test_deterministic_rules_do_not_key_on_fixture_ids(self) -> None:
        source = (
            ROOT / "agents" / "archimedes" / "src" / "repair" / "deterministic.py"
        ).read_text(encoding="utf-8")
        for case_directory in SYNTHETIC_CASES.iterdir():
            if case_directory.is_dir():
                self.assertNotIn(case_directory.name, source)


if __name__ == "__main__":
    unittest.main()
