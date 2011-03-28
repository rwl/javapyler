#!/usr/bin/env jython

import sys
import os
from javapyler import JavaPyler, parser, main
from GWTJavaAstToPythonAst import GWTJavaAstToPythonAst
from GWTPyGen import GWTPyGen
from ConfigParser import ConfigParser


class GWTPyler(JavaPyler):

    def javaAstToPythonAst(self, src, options):
        return GWTJavaAstToPythonAst(src, options)

    def generatePython(self, tree, options):
        pg = GWTPyGen()
        pg.dispatch(tree)
        return pg.getCode()


if __name__ == '__main__':
    options, args = parser.parse_args()
    main(GWTPyler, options, args)
