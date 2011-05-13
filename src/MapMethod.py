from JavaAstToPythonAst import fixme, ast

class MapMethod(object):
    method_map = {
        # java-name : {jtype: (python-name, replace, py-method)}
        'append': {
            'StringBuffer': ('__add__', True, '__add__'),
        },
        'cast': {
            None: (None, False, None),
        },
        'charAt': {
            None: ('_getitem', False, '__getitem__'),
        },
        'contains': {
            'HashSet': ('_in', False, '__contains__'),
            'Set': ('_in', False, '__contains__'),
        },
        'containsKey': {
            None: ('_in', False, '__contains__'),
        },
        'endsWith': {
            None: ('endswith', True, 'endswith'),
        },
        'equals': {
            None: ('_eq', False, '__eq__'),
        },
        'forName': {
            'Class': ('_Class_forName', False, None),
        },
        'get': {
            'ArrayList': ('_getitem', False, '__getitem__'),
            'Field': ('_getattr', False, None),
            'List': ('_getitem', False, '__getitem__'),
            'Map': ('_getitem', False, '__getitem__'),
            'HashMap': ('_getitem', False, '__getitem__'),
            'Hashtable': ('_getitem', False, '__getitem__'),
            'Vector': ('_getitem', False, '__getitem__'),
        },
        'getComponentType': {
            'Class': ('_Class_getComponentType', False, None),
        },
        'getDeclaredFields': {
            'Class': ('_Class_getDeclaredFields', False, None),
        },
        'getField': {
            None: ('_getField', False, None),
        },
        'getSimpleName': {
            'Class': ('_Class_getSimpleName', False, None),
        },
        'getSuperClass': {
            'Class': ('_Class_getSuperClass', False, None),
        },
        'indexOf': {
            None: ('index', True, 'index'),
            'String': ('find', True, 'find'),
        },
        'insert': {
            'StringBuffer': ('%sinsert' % fixme, True, 'insert'),
        },
        'isArray': {
            None: ('_isArray', False, None),
        },
        'isEnum': {
            None: ('_isEnum', False, None),
        },
        'iterator': {
            None: (None, False, None),
        },
        'keySet': {
            None: ('keys', True, 'keys'),
        },
        'lastIndexOf': {
            None: ('rindex', True, 'rindex'),
            'String': ('rfind', True, 'rfind'),
        },
        'length': {
            None: ('len', False, '__len__'),
        },
        'newInstance': {
            None: ('_newInstance', False, None),
            'Array': ('_Array_newInstance', False, None),
        },
        'println': {
            None: ('_println', False, None),
        },
        'readLine': {
            'BufferedReader': ('readline', True, 'readline'),
        },
        'set': {
            'Array': ('_Array_setitem', False, '__setitem__'),
            'Field': ('_Field_setattr', False, '__setitem__'),
        },
        'size': {
            None: ('len', False, '__len__'),
        },
        'startsWith': {
            None: ('startswith', True, 'startswith'),
        },
        'substring': {
            None: ('_getslice', False, '__getslice__'),
        },
        'toArray': {
            None: ('list', False, None),
        },
        'toString': {
            None: ('str', False, '__str__'),
        },
    }

    def mapMethod(self, node, attrname, arguments):
        node_ast = ast.CallFunc
        ast_args = []
        method_map = self.method_map[attrname]
        # Check if the type matches or None in method_map
        # which means any type
        jtypes = self.guessNodeType(node.expr)
        jtypes.append(None)
        for jt in jtypes:
            if jt in method_map:
                break
        else:
            # No match on type. Do nothing.
            return node, arguments, node_ast, ast_args
        name, replace, pymeth = method_map[jt]
        if name is None:
            # Just remove call of method
            return node.expr, None, node_ast, ast_args
        if replace:
            # Replace java method name with python method name
            node.attrname = name
            return node, arguments, node_ast, ast_args
        m = getattr(self, 'mapMethod%s' % name, None)
        if m is not None:
            # Call a specific method for this mapping
            return m(node, arguments)
        elif name == 'str' and len(arguments) == 1:
            node = ast.Name(name)
        else:
            if not arguments:
                n = ast.CallFunc(
                    ast.Name(name),
                    [node.expr],
                )
                n.comments = node.comments
                return n, None, node_ast, ast_args
            else:
                node = ast.Name(name)
        return node, arguments, node_ast, ast_args

    def mapMethod_getitem(self, node, arguments):
        assert len(arguments) == 1
        return node.expr, arguments, ast.Subscript, None

    def mapMethod_setitem(self, node, arguments):
        if len(arguments) != 2:
            #print 'mapMethod_setitem:', self.guessNodeType(node.expr)
            self.warning(None, 'mapMethod_setitem: %r' % arguments)
            return node, arguments, ast.CallFunc, None
        assert len(arguments) == 2
        n = ast.Assign(
            [ast.Subscript(node.expr, 'OP_ASSIGN', arguments[:1])],
            arguments[1],
        )
        return n, None, None, None

    def mapMethod_Array_setitem(self, node, arguments):
        assert len(arguments) == 3
        n = ast.Assign(
            [ast.Subscript(arguments[0], 'OP_ASSIGN', arguments[1:2])],
            arguments[2],
        )
        return n, None, None, None

    def mapMethod_getslice(self, node, arguments):
        if len(arguments) == 1:
            arguments.append(None)
        elif len(arguments) == 2:
            if isinstance(arguments[0], ast.Const) \
               and arguments[0].value == 0:
                arguments[0] = None
            a = arguments[1]
            if (
                isinstance(node.expr, ast.Name)
                and isinstance(a, ast.Sub)
                and isinstance(a.left, ast.CallFunc)
                and isinstance(a.left.node, ast.Name)
                and a.left.node.name == 'len'
                and len(a.left.args) == 1
                and isinstance(a.left.args[0], ast.Name)
                and a.left.args[0].name
            ):
                arguments[1] = ast.UnarySub(a.right)
        else:
            raise TypeError("Invalid number of arguments: %r" % arguments)
        return node.expr, arguments, ast.Slice, None

    def mapMethod_getattr(self, node, arguments):
        #print 'mapMethod_getattr:', self.guessNodeType(node.expr)
        assert len(arguments) == 1
        n = ast.CallFunc(
            ast.Name('getattr'),
            [arguments[0], node.expr],
            None,
            None,
        )
        return n, None, None, None

    def mapMethod_Field_setattr(self, node, arguments):
        assert len(arguments) == 2
        n = ast.CallFunc(
            ast.Name('setattr'),
            [arguments[0], node.expr, arguments[1]],
            None,
            None,
        )
        return n, None, None, None

    def mapMethod_in(self, node, arguments):
        assert len(arguments) == 1
        return arguments[0], [node.expr], ast.Compare, ['in']

    def mapMethod_eq(self, node, arguments):
        assert len(arguments) == 1
        return node.expr, arguments, ast.Compare, ['==']

    def mapMethod_println(self, node, arguments):
        node = ast.Printnl(arguments, None)
        return node, None, None, None

    def mapMethod_newInstance(self, node, arguments):
        return node.expr, arguments, ast.CallFunc, None

    def mapMethod_Array_newInstance(self, node, arguments):
        assert len(arguments) == 2
        node = ast.Mul((ast.List([ast.Name('None')]), arguments[1]))
        return node, None, None, None

    def mapMethod_Class_forName(self, node, arguments):
        assert len(arguments) == 1
        n = ast.CallFunc(
            ast.Lambda(
                ['x'],
                [],
                0,
                ast.CallFunc(
                    ast.Name('getattr'),
                    [
                        ast.CallFunc(
                            ast.Name('__import__'),
                            [
                                ast.Subscript(
                                    ast.CallFunc(
                                        ast.Getattr(ast.Name('x'), 'rsplit'),
                                        [ast.Const('.'), ast.Const(1)],
                                        None,
                                        None,
                                    ),
                                    'OP_APPLY',
                                    [ast.Const(0)],
                                ),
                                ast.Keyword(
                                    'fromlist',
                                    ast.Subscript(
                                        ast.CallFunc(
                                            ast.Getattr(ast.Name('x'), 'rsplit'),
                                            [ast.Const('.'), ast.Const(1)],
                                            None,
                                            None,
                                        ),
                                        'OP_APPLY',
                                        [ast.Const(0)],
                                    ),
                                ),
                            ],
                            None,
                            None,
                        ),
                        ast.Subscript(
                            ast.CallFunc(
                                ast.Getattr(ast.Name('x'), 'split'),
                                [ast.Const('.')],
                                None,
                                None,
                            ),
                            'OP_APPLY',
                            [ast.UnarySub(ast.Const(1))],
                        ),
                    ],
                    None,
                    None,
                ),
            ),
            arguments,
            None,
            None,
        )
        return n, None, None, None

    def mapMethod_Class_getComponentType(self, node, arguments):
        assert len(arguments) == 0
        return node.expr, [ast.Const(0)], ast.Subscript, None

    def mapMethod_Class_getDeclaredFields(self, node, arguments):
        assert len(arguments) == 0
        n = ast.ListComp(
            ast.Name('a'),
            [
                ast.ListCompFor(
                    ast.AssName('a', 'OP_ASSIGN'),
                    ast.CallFunc(
                        ast.Name('dir'),
                        [node.expr],
                        None,
                        None,
                    ),
                    [
                        ast.ListCompIf(
                            ast.Not(
                                ast.Compare(
                                    ast.Slice(
                                        ast.Name('a'),
                                        'OP_APPLY',
                                        None,
                                        ast.Const(2),
                                    ),
                                    [('==', ast.Const('__'))],
                                ),
                            ),
                        ),
                    ],
                ),
            ],
        )
        return n, None, None, None

    def mapMethod_Class_getSimpleName(self, node, arguments):
        assert len(arguments) == 0
        n = ast.Getattr(node.expr, ast.Name('__name__'))
        return n, None, None, None

    def mapMethod_Class_getSuperClass(self, node, arguments):
        assert len(arguments) == 0
        n = ast.CallFunc(ast.Name('super'), [node.expr], None, None)
        return n, None, None, None

    def mapMethod_getField(self, node, arguments):
        assert len(arguments) == 1
        return arguments[0], None, None, None

    def mapMethod_isArray(self, node, arguments):
        assert len(arguments) == 0
        n = ast.CallFunc(
            ast.Name('isinstance'),
            [node.expr, ast.Name('list')],
            None,
            None,
        )
        return n, None, None, None

    def mapMethod_isEnum(self, node, arguments):
        assert len(arguments) == 0
        n = ast.CallFunc(
            ast.Name('hasattr'),
            [node.expr, ast.Const(self.enum_values_name)],
            None,
            None,
        )
        return n, None, None, None
