# Copyright (c) 2020, 2024, 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import os
import shlex
import sys
from collections.abc import Iterable
from typing import Any


class ArgumentParser(argparse.ArgumentParser):
    """Argument parser which supports config files via '@' prefix (by default)."""

    def __init__(
        self,
        tool: str | None = None,
        comments: bool = False,
        arg_file_path: list[str] | None = None,
        fromfile_prefix_chars: str | None = None,
        epilog: str = "",
        formatter_class: type[argparse.HelpFormatter] = argparse.RawTextHelpFormatter,
        **kwargs: Any,
    ):
        self.enable_comments = comments
        self.fromfile_prefix_chars = fromfile_prefix_chars or "@"
        self._arg_file_path = arg_file_path or ["."]

        epilog = (
            f"""
Config file options:
    Additional arguments can be inserted from config files with `[{self.fromfile_prefix_chars}]<filename>`.

    For example:
        {os.path.basename(tool or kwargs.get("prog", "<tool>"))} {self.fromfile_prefix_chars[0]}something.cfg

    Will insert all arguments within the "something.cfg" file, which could be in any of the following:
        {", ".join('"' + f + '"' for f in self._arg_file_path)}

"""
            + epilog
        )

        super().__init__(
            epilog=epilog,
            fromfile_prefix_chars=self.fromfile_prefix_chars,
            formatter_class=formatter_class or argparse.RawTextHelpFormatter,
            **kwargs,
        )

    def _read_args_from_files(self, arg_strings: Iterable[str], context: list[str] | None = None) -> list[str]:
        # expand arguments referencing files
        new_arg_strings = []
        for arg_string in arg_strings:
            # for regular arguments, just add them back into the list
            if not arg_string or arg_string[0] not in self.fromfile_prefix_chars:
                new_arg_strings.append(arg_string)
                continue

            # replace arguments referencing files with the file content
            from_path = os.path.realpath(os.path.dirname(context[-1])) if context else None
            context = context or []
            try:
                config_path = arg_string[1:]
                if from_path is None:
                    search_paths = [os.path.join(path, config_path) for path in self._arg_file_path]
                else:
                    search_paths = [os.path.join(from_path, config_path)]

                found = False
                for path in search_paths:
                    try:
                        with open(path) as args_file:
                            for context_path in context:
                                if os.path.realpath(path) == os.path.realpath(context_path):
                                    raise ValueError(
                                        f"Inclusion loop, including {path} within {config_path}, previously from {context_path}"
                                    )

                            new_arg_strings.extend(
                                self._read_args_from_files(
                                    [arg for arg_line in args_file for arg in self.convert_arg_line_to_args(arg_line)],
                                    context=context + [path],
                                )
                            )
                        found = True
                        break
                    except FileNotFoundError:
                        pass

                if not found:
                    raise FileNotFoundError(f"No such file or directory: {config_path}")

            except OSError:
                err = sys.exc_info()[1]
                self.error(str(err))

        # return the modified argument list
        return new_arg_strings

    def convert_arg_line_to_args(self, arg_line: str) -> list[str]:
        return shlex.split(arg_line, comments=self.enable_comments)
