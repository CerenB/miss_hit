#!/usr/bin/env python3
##############################################################################
##                                                                          ##
##          MATLAB Independent, Small & Safe, High Integrity Tools          ##
##                                                                          ##
##              Copyright (C) 2020, Florian Schanda                         ##
##                                                                          ##
##  This file is part of MISS_HIT.                                          ##
##                                                                          ##
##  MATLAB Independent, Small & Safe, High Integrity Tools (MISS_HIT) is    ##
##  free software: you can redistribute it and/or modify it under the       ##
##  terms of the GNU General Public License as published by the Free        ##
##  Software Foundation, either version 3 of the License, or (at your       ##
##  option) any later version.                                              ##
##                                                                          ##
##  MISS_HIT is distributed in the hope that it will be useful,             ##
##  but WITHOUT ANY WARRANTY; without even the implied warranty of          ##
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           ##
##  GNU General Public License for more details.                            ##
##                                                                          ##
##  You should have received a copy of the GNU General Public License       ##
##  along with MISS_HIT. If not, see <http://www.gnu.org/licenses/>.        ##
##                                                                          ##
##############################################################################

# This is a small helper tool that, when given a directory, will
# display all Simulink block types in all slx models in that
# directory.

import os
import argparse

import command_line
import config
from errors import Message_Handler
from s_parser import Simulink_SLX_Parser


def process(mh, root_dir, file_name):
    # pylint: disable=unused-argument
    # short_name = file_name[len(root_dir.rstrip("/")) + 1:]

    mh.register_file(file_name)

    rv = set()

    slp = Simulink_SLX_Parser(mh, file_name, config.BASE_CONFIG)
    n_container = slp.parse_file()
    if n_container:
        for n_block in n_container.iter_all_blocks():
            rv.add(n_block.kind)

    return rv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root_dir")

    options = ap.parse_args()

    mh = Message_Handler("debug")
    mh.sort_messages = False

    all_block_kinds = set()

    for path, _, files in os.walk(options.root_dir):
        for f in files:
            if f.endswith(".slx"):
                all_block_kinds |= process(mh,
                                           options.root_dir,
                                           os.path.join(path, f))

    print("Sorted list of all Simulink blocks types (%u) present:" %
          len(all_block_kinds))
    for block_type in sorted(all_block_kinds):
        print("   %s" % block_type)

    mh.summary_and_exit()


if __name__ == "__main__":
    command_line.ice_handler(main)
