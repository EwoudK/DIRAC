""" Basic setuptools script for DIRACDocs.
    Does not contain any dependency
"""

import sys
import os
import glob
# Actual setuptools
from setuptools import setup, find_packages

# Find the base dir where the setup.py lies
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'diracdoctools'))
print 'BASE DIR', base_dir
# Take all the packages but the scripts and tests
ALL_PACKAGES = find_packages(where=base_dir, exclude=["*test*", "*scripts*"])
print 'CHRIS allPackages %s' % ALL_PACKAGES

# Because we want to have a 'DIRAC' base module and that the setup.py
# is lying inside it, we need to define a mapping
# < module name : directory >
# e.g. DIRAC.DataManagementSystem is base_dir/DataManagementSystem

package_dir = dict(("%s" % p, os.path.join(base_dir, p.replace('.', '/'))) for p in ALL_PACKAGES)
print 'CHRIS package_dir %s' % package_dir

# We also rename the packages so that they contain diracdoctools
ALL_PACKAGES = ['%s' % p for p in ALL_PACKAGES]
print 'CHRIS allPackages %s' % ALL_PACKAGES

# Artificially create the 'DIRAC' package
# at the root
ALL_PACKAGES.insert(0, 'diracdoctools')
package_dir['diracdoctools'] = base_dir

# The scripts to be distributed
SCRIPTS = glob.glob('%s/scripts/*.py' % base_dir)
print 'CHRIS scripts %s' % SCRIPTS
setup(
    name="diracdoctools",
    version="6.19.0",
    url="https://github.com/DIRACGRID/DIRAC/docs",
    license="GPLv3",
    package_dir=package_dir,
    packages=ALL_PACKAGES,
    scripts=SCRIPTS,
)
