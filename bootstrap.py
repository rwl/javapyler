#!/usr/bin/env python

import os

base = __file__
base = os.path.realpath(os.path.abspath(base))
base = os.path.dirname(base)
javapyler = os.path.join('bin', 'javapyler')
gwtpyler = os.path.join('bin', 'gwtpyler')

if not os.path.isdir(os.path.join(base, 'bin')):
    os.mkdir(os.path.join(base, 'bin'))

open(javapyler, 'w').write('''\
#!/bin/sh

BASE="%s"
JAVAPYLER="%s"
#export JAVA=/usr/bin/java
#export JAVA_OPTIONS="-Xms64m"

jython ${JAVAPYLER} $@
''' % (
    base,
    os.path.join(base, 'src', 'javapyler.py'),
))

open(gwtpyler, 'w').write('''\
#!/bin/sh

BASE="%s"
GWTPYLER="%s"

jython ${GWTPYLER} $@
''' % (
    base,
    os.path.join(base, 'src', 'gwtpyler.py'),
))

os.chmod(javapyler, 0755)
os.chmod(gwtpyler, 0755)
