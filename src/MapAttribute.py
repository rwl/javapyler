from JavaAstToPythonAst import fixme, ast


class MapAttribute(object):
    attrib_map = {
        'length': {
            'String': ('len', False, '__len__'),
            'Vector': ('len', False, '__len__'),
        }
    }

    def mapAttrib(self, node, attrname=None):
        if attrname is None:
            attrname = node.attrname
        attrib_map = self.attrib_map[attrname]
        jtypes = self.guessNodeType(node.expr)
        jtypes.append(None)
        for jt in jtypes:
            if jt in attrib_map:
                break
        else:
            return node
        name, replace, pyname = attrib_map[jt]
        if replace:
            node.attrname = name
            return node
        n = ast.CallFunc(
            ast.Name(name),
            [node.expr]
        )
        n.comments = node.comments
        return n
