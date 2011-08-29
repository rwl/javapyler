#!/usr/bin/env jython

import sys
import os
import time
import codecs
# There's no optparse in jython
#import getopt
try:
    from optparse import OptionParser
except ImportError:
    from py2.optparse import OptionParser
from ConfigParser import ConfigParser, NoOptionError

from JavaAstToPythonAst import JavaAstToPythonAst
from PyGen import PyGen

timing = {}


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
                if src.find(section) >= 0:
                    for k in opts.config.options(section):
                        try:
                            v = opts.config.getboolean(section, k)
                        except (NoOptionError, ValueError):
                            v = opts.config.get(section, k)
                            try:
                                v = int(v)
                            except:
                                pass
                        setattr(opts, k, v)
        # Now the real stuff
        t = time.time()
        jp = self.javaAstToPythonAst(src, opts)
        timings = jp.timings()
        timings['javaAstToPythonAst'] = time.time() - t
        t = time.time()
        pycode = self.generatePython(jp.module, opts)
        output = opts.output
        if not output:
            dst = os.path.join(output, src)
        elif dst:
            dst = os.path.join(output, dst)
        elif not opts.java_base:
            dst = os.path.join(output, src)
        else:
            dst = opts.java_base.split('.')
            dst = "%s%s" % (os.path.join(*dst), os.sep)
            i = src.find(dst)
            if i < 0:
                dst = src
            else:
                dst = os.path.join(output, src[i+len(dst):])
        if dst.endswith('java'):
            dst = "%s.py" % dst[:-5]
        assert src != dst
        self.output(dst, pycode)
        timings['generatePython'] = time.time() - t
        for k, v in timings.iteritems():
            if k in timing:
                timing[k] += v
            else:
                timing[k] = v

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
            fp = codecs.getwriter('UTF-8')(fp)
            fp.write("# -*- coding: utf-8 -*-\n")
            fp.write(pycode)
            fp.close()


def handle_config_option(option, opt, value, parser, *args, **kwargs):
    fp = open(value, 'r')
    del fp
    parser.values.config_file = value
    config = ConfigParser()
    parser.values.config = config
    config.read(value)
    for k, v in config.defaults().iteritems():
        try:
            v = config.getboolean('DEFAULT', k)
        except ValueError:
            pass
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
    if options.timing:
        print timing


parser = OptionParser()
parser.add_option(
    '--javalib',
    dest='javalib',
    help='module with java functions library',
)
parser.add_option(
    '--java-base',
    dest='java_base',
    help='base java import to be ignored in python imports',
)
parser.add_option(
    '--java-base-path',
    dest='java_base_path',
    help='path that matches java_base',
)
parser.add_option(
    '--py-base',
    dest='py_base',
    help='python base import for imports that match the java base',
)
parser.add_option(
    '--add-get-package',
    dest='add_get_package',
    action='store_true',
    help='add getPackage)() method to classes',
)
parser.add_option(
    '--as-module',
    dest='as_module',
    help='promotes the base class(es) to module level',
)
parser.add_option(
    '--output',
    dest='output',
    help='base directory for generated python files',
)
parser.add_option(
    '--timing',
    dest='timing',
    action='store_true',
    help='collect and print timing characteristics',
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
