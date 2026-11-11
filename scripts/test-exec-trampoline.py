#!/usr/bin/env python3
"""Compile the real table parser with a host loader and inspect its argument vector."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).with_name("exec-trampoline.c")


class ExecTrampolineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        source = SOURCE.read_text()
        declaration = 'static const char *kLoader = "/system/bin/linker64";'
        assert source.count(declaration) == 1
        source = source.replace(declaration, f"static const char *kLoader = {json.dumps(sys.executable)};")
        system_dir = 'static const char *kSystemDir = "/system/";'
        assert source.count(system_dir) == 1
        cls.system = cls.root / "system"
        cls.system.mkdir()
        source = source.replace(
            system_dir, f"static const char *kSystemDir = {json.dumps(str(cls.system) + '/')};"
        )
        c_file = cls.root / "trampoline.c"
        c_file.write_text(source)
        cls.command = cls.root / "java"
        subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", str(c_file), "-o", str(cls.command)], check=True)
        cls.target = cls.root / "dump.py"
        cls.target.write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")

    def invoke(self, row_args, caller_args=()):
        table = self.root / "table.tsv"
        table.write_text("\tTRAMPOLINE_FIXTURE\tkept\n" + "\t".join(["java", str(self.target), *row_args]) + "\n")
        return subprocess.run(
            [str(self.command), *caller_args],
            env={**os.environ, "VSCODROID_EXEC_TABLE": str(table)},
            capture_output=True, text=True,
        )

    def test_options_precede_caller_arguments_without_splitting(self):
        result = self.invoke(["-XX:UseSVE=0", "-Duser.home=/home/a b", "-Djava.io.tmpdir=/tmp/app"], ["file name.java", "x"])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["-XX:UseSVE=0", "-Duser.home=/home/a b", "-Djava.io.tmpdir=/tmp/app", "file name.java", "x"],
            json.loads(result.stdout),
        )

    def test_target_only_and_legacy_script_rows_still_work(self):
        for row in [[], ["script path.sh"]]:
            result = self.invoke(row, ["caller"])
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(row + ["caller"], json.loads(result.stdout))

    def test_eight_arguments_are_kept_including_an_empty_one(self):
        args = ["", "2", "3", "4", "5", "6", "7", "8"]
        result = self.invoke(args)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(args, json.loads(result.stdout))

    def test_ninth_argument_refuses_the_whole_row(self):
        result = self.invoke([str(n) for n in range(9)])
        self.assertEqual(127, result.returncode)
        self.assertIn("too many arguments", result.stderr)
        self.assertEqual("", result.stdout)

    def test_a_system_target_is_executed_directly_not_through_the_loader(self):
        # Through the loader the target would be read as Python source and its
        # arguments printed as JSON; exec'd directly it runs as the shell script
        # it is, which is the only way this output can appear.
        program = self.system / "sh"
        program.write_text('#!/bin/sh\nprintf "direct:%s:%s\\n" "$1" "$2"\n')
        program.chmod(0o755)
        table = self.root / "system-table.tsv"
        table.write_text("\t".join(["java", str(program), "kept script.sh"]) + "\n")
        result = subprocess.run(
            [str(self.command), "caller"],
            env={**os.environ, "VSCODROID_EXEC_TABLE": str(table)},
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("direct:kept script.sh:caller\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
