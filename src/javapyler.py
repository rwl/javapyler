#!/usr/bin/env jython

import sys
import os
# There's no optparse in jython
#import getopt
try:
    from optparse import OptionParser
except ImportError:
    from py2.optparse import OptionParser
from ConfigParser import ConfigParser

from JavaAstToPythonAst import JavaAstToPythonAst
from PyGen import PyGen


class JavaPyler(object):
    def __init__(self, src, dst, options):
        self.src = src
        self.dst = dst
        # Make a copy of options
        class Opts(object):
            pass
        opts = Opts()
        for k in dir(options):
            if k.startswith('_'):
                continue
            v = getattr(options, k)
            if callable(v):
                continue
            setattr(opts, k, v)
        self.options = opts
        # Override options, if available
        if opts.config is not None:
            for section in opts.config.sections():
                if src.find(section):
                    for k in opts.config.options(section):
                        v = opts.config.get(section, k)
                        vlower = v.lower()
                        if vlower == 'true':
                            v = True
                        elif vlower == 'false':
                            v = False
                        else:
                            try:
                                v = int(v)
                            except:
                                pass
                        setattr(opts, k, v)
        # Now the real stuff
        jp = self.javaAstToPythonAst(src, options)
        pycode = self.generatePython(jp.module, options)
        output = options.output
        if output:
            if dst:
                dst = os.path.join(output, dst)
            else:
                dst = os.path.join(output, src)
                dst = "%s.py" % dst[:-5]
        self.output(dst, pycode)

    def javaAstToPythonAst(self, src, options):
        return JavaAstToPythonAst(src, options)

    def generatePython(self, tree, options):
        pg = PyGen()
        pg.dispatch(tree)
        return pg.getCode()

    def output(self, dst, pycode):
        if dst is None:
            print pycode
        else:
            dirname = os.path.dirname(dst)
            if not os.path.isdir(dirname):
                os.makedirs(dirname)
            fp = open(dst, 'w')
            fp.write(pycode)
            fp.close()


def handle_config_option(option, opt, value, parser, *args, **kwargs):
    parser.values.config_file = value
    config = ConfigParser()
    parser.values.config = config
    config.read(value)
    for k, v in config.defaults().iteritems():
        setattr(parser.values, k, v)

def parse(parser_class, src, dst, options):
    try:
        parser_class(src, dst, options)
    except:
        sys.stderr.write("Error on %s\n" % src)
        raise

def main(parser_class, options, args):
    if not hasattr(options, 'config'):
        setattr(options, 'config', None)
    if len(args) == 0:
        src = getattr(options, 'src', None)
        if src:
            args = [a.strip() for a in src.split(',')]
    for arg in args:
        if os.path.isdir(arg):
            for f in os.listdir(arg):
                if f.endswith(".java"):
                    fpath = os.path.join(arg, f)
                    parse(parser_class, fpath, None, options)
        else:
            parse(parser_class, arg, None, options)

parser = OptionParser()
parser.add_option(
    '--javalib',
    dest='javalib',
    help='module with java functions library',
)
parser.add_option(
    '--java-base',
    dest='java_base',
    help='base java import to be ignored in python imports'
)
parser.add_option(
    '--py-base',
    dest='py_base',
    help='python base import for imports that match the java base'
)
parser.add_option(
    '--as-module',
    dest='as_module',
    help='promotes the base class(es) to module level'
)
parser.add_option(
    '--output',
    dest='output',
    help='base directory for generated python files'
)

parser.add_option(
    "--config-file",
    dest="config_file",
    action="callback",
    callback=handle_config_option,
    type="string",
    help="configuration file"
)

if __name__ == '__main__':
    options, args = parser.parse_args()
    main(JavaPyler, options, args)
