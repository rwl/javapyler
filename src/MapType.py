from JavaAstToPythonAst import fixme, ast


class MapType(object):
    type_map = {
        # java type: (py-type, py-import)
        'ArrayList': ('list', None),
        'Boolean': ('bool', None),
        'BufferedReader': ('StringIO', ast.TryExcept(
                ast.Stmt([ast.From('cStringIO', [('StringIO', None)], 0)]),
                [(
                    ast.Name('ImportError'),
                    ast.AssName('e', 'OP_ASSIGN'),
                    ast.Stmt([ast.From('StringIO', [('StringIO', None)], 0)]),
                )],
                None)),
        'Byte': ('int', None),
        'byte': ('int', None),
        'Double': ('float', None),
        'double': ('float', None),
        'Exception': ('Exception', None),
        'Float': ('float', None),
        'HashMap': ('dict', None),
        'Hashtable': ('dict', None),
        'HashSet': ('set', None),
        'int': ('int', None),
        'Int': ('int', None),
        'Integer': ('int', None),
        'Iterable': ('object', None),
        'List': ('list', None),
        'Long': ('long', None),
        'Map': ('dict', None),
        'NumberFormatException': ('ValueError', None),
        'RuntimeException': ('RuntimeError', None),
        'Set': ('set', None),
        'Short': ('int', None),
        'String': ('str', None),
        'StringBuffer': ('str', None),
        'Throwable': ('BaseException', None),
        'Vector': ('list', None),
    }

    def mapType(self, type_):
        py = self.type_map.get(type_)
        if py is None:
            return type_
        pytype, pyimport = py
        if pyimport != None and not pyimport in self.pyimports:
            self.pyimports.append(pyimport)
        return pytype
