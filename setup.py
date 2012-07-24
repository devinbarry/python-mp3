# vim:syntax=python:textwidth=0
#
# MP3 Tools -- Setup script for Python MP3 tools
# Copyright (C) 2004  Sune Kirkeby
#               2012  Lorenz Bauer
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#

from setuptools import setup
import os
import sys

if sys.version_info < (2, 7, 0):
    # We depend on memoryviews that only become available with 2.7
    sys.stderr.write("python-mp3 requires at least Python 2.7\n")
    sys.exit(-1)

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

if sys.platform.startswith('darwin') or sys.platform.startswith('linux'):
    install_requires = ['xattr>=0.6.4']
else:
    install_requires = []

setup(name = "MP3 Tools", version = "0.2",
      author = "Lorenz Bauer, Sune Kirkeby",
      url = "https://github.com/lmb/python-mp3",
      scripts = [
        'src/repair-mp3',
        'src/test-mp3',
        'src/dump-id3',
        'src/sanitize-mp3'
      ],
      packages = [
        'mp3', 'mp3.tests',
        'id3',
      ],
      package_dir = { '': 'src' },
      test_suite = 'mp3.tests.suite',
      install_requires = install_requires
    )
