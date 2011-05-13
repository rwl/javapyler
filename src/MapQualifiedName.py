from JavaAstToPythonAst import fixme, ast


class MapQualifiedName(object):
    qualifiedName_map = {
        'java.lang.Boolean': ('bool', None),
        'java.lang.Byte': ('int', None),
        'java.lang.Float': ('float', None),
        'java.lang.Integer': ('int', None),
        'java.lang.Long': ('int', None),
        'java.lang.Short': ('int', None),
        'java.lang.String': ('str', None),
        'Byte.parseByte': ('int', None),
        'Float.parseFloat': ('float', None),
        'Integer.parseInt': ('int', None),
        'Long.parseLong': ('int', None),
        'Short.parseShort': ('int', None),
        'System.in': ('sys.stdin', ast.Import([('sys', None)])),
        'System.out': ('sys.stdout', ast.Import([('sys', None)])),
        'System.out.print': ('sys.stdout.write', ast.Import([('sys', None)])),
        # this is very dirty: 1000 * time.time
        'System.currentTimeMillis': ('1000 * time.time', ast.Import([('time', None)])),
    }

    def mapQualifiedName(self, name):
        py = self.qualifiedName_map.get(name)
        if py is None:
            return name
        pyname, pyimport = py
        if pyimport != None and not pyimport in self.pyimports:
            self.pyimports.append(pyimport)
        self.block_self_scope = True
        return pyname
