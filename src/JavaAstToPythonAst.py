#!/usr/bin/env jython

import sys
import os
base = __file__
base = os.path.abspath(base)
base = os.path.dirname(base)
sys.path.append(os.path.abspath(os.path.join(
    base, "javaparser", "antlr-3.3-complete.jar",
)))
sys.path.append(os.path.abspath(os.path.join(
    base, "javaparser", "javaparser.jar",
)))
import re
from py2 import ast
import JavaLexer
import JavaParser
from javaparser.JavaWalker import JavaWalker
import javaparser.JavaAst as jast
from org.antlr.runtime import ANTLRFileStream, CommonTokenStream
from fixes import *


# Monkey patch ast.None to have a default comments
ast.Node.comments = None


file_globals = {}
file_globals_guess = {}


class State(object):

    def __init__(self, **kwargs):
        self.kw = kwargs.keys()
        for k, v in kwargs.iteritems():
            setattr(self, k, v)


class AstError(Exception):

    def __init__(self, java_ast, *args):
        self.java_ast = java_ast
        self.args = args

    def __repr__(self):
        if len(self.args) == 1:
            args = self.args[0]
        else:
            args = self.args
        return "AstError(%r, %s, %s)" % (
            self.java_ast,
            self.java_ast.lineno,
            args,
        )

    def __str__(self):
        return self.__repr__()


class AstInitArgs(Exception):

    def __init__(self, is_this, java_init, py_init, node):
        if is_this:
            self.is_this = True
        else:
            self.is_this = False
        self.java_init = java_init
        self.py_init = py_init
        self.node = node
        self.args = [is_this, java_init, py_init, node]

    def __repr__(self):
        return "AstInitArgs(%r, %r)" % (self.is_this, self.java_init)


class IgnoreMethod(Exception):
    pass


class IgnoreInterface(Exception):
    pass


class AnonymousClass(object):

    def __init__(self, cls_node, java_node, this_suffixes):
        self.cls_node = cls_node
        self.java_node = java_node
        self.this_suffixes = this_suffixes
        #assert cls_node.nodes > 0


class Type(object):

    def __init__(self, name):
        self.name = name


class JavaAstToPythonAst(object):
    log_level = 0
    type_map = {
        'ArrayList': 'list',
        'Double': 'float',
        'HashMap': 'dict',
        'int': 'int',
        'Int': 'int',
        'Integer': 'int',
        'List': 'list',
        'Map': 'dict',
        'String': 'str',
    }
    # toArray -> list()
    # toString -> str()
    # length -> len()
    # size -> len()
    # charAt -> []
    # .get -> []
    # indexOf -> index
    method_map = {
        # java-name : (python-name, replace, py-method)
        'cast': (None, False, None),
        'charAt': ('[]', False, '__getitem__'),
        'containsKey': ('has_key', True, 'has_key'),
        'equals': ('==', False, '__eq__'),
        #'get': ('[]', False, '__getitem__'),
        'indexOf': ('index', True, 'index'),
        'keySet': ('keys', True, 'keys'),
        'length': ('len', False, '__len__'),
        'size': ('len', False, '__len__'),
        'toArray': ('list', False, None),
        'toString': ('str', False, '__str__'),
    }
    scoped_ignore = [
        'self',
        'super',
        'System',
    ]

    def __init__(self, srcFile, options=None,**kwargs):
        if options is None:
            self.opts = object()
        else:
            self.opts = options
        for k, v in kwargs.iteritems():
            setattr(self.opts, k, v)
        self.srcFile = srcFile
        self.absSrcFile = os.path.realpath(os.path.abspath(srcFile))
        lexer = JavaLexer(ANTLRFileStream(srcFile))
        tokens = CommonTokenStream(lexer)
        parser = JavaParser(tokens)
        cu = parser.compilationUnit()
        tree = cu.getTree()
        t = tree.getToken()
        walker = JavaWalker()
        cu = walker.walk(tree, tokens)

        self.locals = [{}]
        self.globals = {}
        self.globals_ref = {}
        self.class_names = []
        self.methods = [{}]
        self.tmpvars = [{}]
        self.analysing = 0
        self.ign_expl_constr = False
        self.raise_expl_constr = True
        self.this_suffixes = []
        self.state_stack = []
        self.label_stack = []
        self.comments = []
        self.javadocs = []
        self.javalib = []
        self.block_self_scope = False
        for i in range(tokens.size()):
            t = tokens.get(i)
            if t.getChannel() == t.HIDDEN_CHANNEL:
                if t.getText().startswith("/**"):
                    self.javadocs.append(t)
                else:
                    self.comments.append(t)

        if cu.package is None:
            self.package_name = None
        else:
            self.package_name = cu.package.name
        stmt = ast.Stmt([], 1)
        self.module = ast.Module(None, stmt, None)
        self.descend()
        doc = None
        if len(cu.types) == 0:
            self.flatten_stmt(stmt, True)
            return
        t = cu.types[0]
        for c in self.javadocs:
            if c.getLine() >= t.lineno:
                break
            cs = c.getText()
            if doc is None and cs[3] == "/**":
                doc = self.setDoc(self.module, c)
        nodes = []
        if cu.comments:
            stmt.nodes.append(self.node(cu, ast.EmptyNode()))
        self.collectGlobals(cu)
        # Need to be set after self.collectGlobals():
        self.class_names = [{}]

        for t in cu.types:
            try:
                nodes.append(self.dispatch(t))
            except IgnoreInterface:
                pass

        if self.javalib:
            self.javalib.sort()
            javalib = getattr(self.opts, 'javalib')
            if javalib is None:
                javalib = 'javapyler.java'
            stmt.nodes.append(ast.From(javalib, self.javalib, 0, None))

        self.createImports(stmt, cu)
        stmt.nodes += nodes
        self.flatten_stmt(stmt, True)

    def pushState(self):
        self.state_stack.append(State(
            globals=self.globals,
            locals=self.locals,
            analysing=self.analysing,
            tmpvars=self.tmpvars,
        ))

    def popState(self):
        self.pushState()
        old_state = self.state_stack.pop()
        state = self.state_stack.pop()
        for k in state.kw:
            setattr(self, k, getattr(state, k))
        return old_state

    def getStackDepth(self):
        len(self.state_stack)

    def setStackDepth(self, depth):
        assert len(self.state_stack) >= depth
        self.state_stack = self.state_stack[:depth]

    def addJavaLib(self, name):
        if not name in self.javalib:
            self.javalib.append(name)

    def addGlobal(self, name, pnode, jtype):
        if not name in self.globals:
            self.globals[name] = [pnode, jtype]
            if self.getClassDepth() == 1:
                if not self.absSrcFile in file_globals:
                    file_globals[self.absSrcFile] = {}
                file_globals[self.absSrcFile][name] = self.globals[name]
        else:
            if self.globals[name][0] is None:
                self.globals[name][0] = pnode
            if self.globals[name][1] is None:
                self.globals[name][1] = jtype

    def hasGlobal(self, name):
        if name in self.globals:
            self.globals_ref[name] = 1
            return True
        return False

    def addLocal(self, name, pnode, jtype):
        if self.opts.as_module:
            if self.getClassDepth() <= 2:
                return self.addGlobal(name, pnode, jtype)
        if not name in self.locals[-1]:
            self.locals[-1][name] = [pnode, jtype]
        else:
            if self.locals[-1][name][0] is None:
                self.locals[-1][name][0] = pnode
            if self.locals[-1][name][1] is None:
                self.locals[-1][name][1] = jtype

    def getLocal(self, name):
        if self.opts.as_module:
            if name in self.locals[-1]:
                return self.locals[-1][name]
            return self.globals[name]
        return self.locals[-1][name]

    def hasLocal(self, name):
        return name in self.locals[-1]

    def pushClassName(self, name):
        self.methods.append({})
        self.class_names.append(name)

    def popClassName(self):
        self.methods.pop()
        return self.class_names.pop()

    def getClassName(self, level=-1):
        return self.class_names[level]

    def getClassDepth(self):
        return len(self.class_names)

    def addMethod(self, name, **kwargs):
        methods = self.methods[-1]
        if not name in methods:
            methods[name] = dict(count=0, nargs={})
        method = methods[name]
        # java_args : arguments in java definition
        # java_init : None or arguments in this() call
        # java_body : java ast of method body
        # py_init
        # py_body
        m = dict(
            node=None,
        )
        m.update(kwargs)
        if m['java_args'] is None:
            nargs = 0
            m['java_args'] = []
        else:
            nargs = len(m['java_args'])
        if not nargs in method['nargs']:
            method['nargs'][nargs] = []
        method['nargs'][nargs].append(m)
        method['count'] += 1
        if not 'defaults' in m:
            m['defaults'] = [None for i in range(nargs)]
        if not 'java_init' in m:
            m['java_init'] = None
        if not 'py_init' in m:
            m['py_init'] = None

    def getMethod(self, name, java_args=False):
        method = self.methods[-1].get(name, None)
        if java_args is False or method is None:
            return method
        if java_args is None or len(java_args) == 0:
            if len(method['nargs'][0]) == 1:
                return method['nargs'][0][0]
            return method['nargs'][0]
        nargs = len(java_args)
        for m in method['nargs'][nargs]:
            if m['java_args'] == java_args:
                return m
        return None

    def setMethod(self, name, method=None):
        if method is None:
            del(self.methods[-1][name])
        else:
            self.methods[-1][name] = method

    def getTmpVar(self, init=None, level=-1):
        if level < 0:
            level = len(self.tmpvars) + level
        if level == 0:
            varname = "_%d_"
        else:
            varname = "_%d"
        varname = varname % len(self.tmpvars[level])
        if not self.analysing:
            self.tmpvars[level][varname] = init
        if init is not None:
            if isinstance(init, AnonymousClass):
                raise AstError(e, "AnonymousClass")
            init = ast.Assign(
                [ast.AssName(varname, 'OP_ASSIGN')],
                init,
            )
        return varname, init

    def mapType(self, type_):
        return self.type_map.get(type_, type_)

    def descend(self):
        self.locals.append({})
        self.tmpvars.append({})

    def ascend(self):
        self.locals.pop()
        self.tmpvars.pop()

    def log(self, level, *args):
        if self.log_level >= level:
            line = [repr(a) for a in args]
            line = ' '.join(line)
            sys.stderr.write("%s\n" % line)

    def warning(self, lineno, *args):
        line = [repr(a) for a in args]
        line = ' '.join(line)
        sys.stderr.write("%s(%s): %s\n" % (self.srcFile, lineno, line))

    def parseComment(self, comment):
        if comment is None:
            return None
        if not isinstance(comment, basestring):
            comment = str(comment)
        lines = []
        for line in comment.split("\n"):
            sline = line.lstrip()
            if line.startswith("/*"):
                line = line[2:]
                if line.endswith('*/'):
                    line = line[:-2]
                line = line.lstrip("*")
                if line:
                    lines.append(line)
            elif sline == "*":
                lines.append("")
            elif line.endswith("*/"):
                line = line[:-2]
                lines.append(line)
            elif sline.startswith("* "):
                lines.append(sline[2:])
            elif line.startswith("//"):
                lines.append(line[2:].rstrip())
            else:
                lines.append(line)
        while len(lines) > 0:
            if lines[-1].strip() == '':
                lines.pop()
            elif lines[0].strip() == '':
                lines.pop(0)
            else:
                break
        if not lines:
            return None
        return "\n".join(lines)

    def setDoc(self, node, comment):
        if comment in self.comments:
            self.comments.remove(comment)
        node.doc = self.parseComment(comment)

    def collectGlobals(self, cu):
        def handleFile(fpath, impname=None):
            if fpath == self.absSrcFile:
                return
            if fpath in file_globals:
                for name in file_globals[fpath]:
                    pnode, jtype = file_globals[fpath][name]
                    self.addGlobal(name, None, jtype)
                return
            if fpath in file_globals_guess:
                for name in file_globals_guess[fpath]:
                    self.addGlobal(name, fpath, None)
                return
            if impname is not None and impname in file_globals_guess:
                for name in file_globals_guess[impname]:
                    self.addGlobal(name, impname, None)
                return
            file_globals_guess[fpath] = {}
            if os.path.isfile(fpath):
                data = open(fpath, 'r').read()
                for regex in [re_class, re_enum, re_interface]:
                    for r in regex.findall(data):
                        file_globals_guess[fpath][r] = None
                        self.addGlobal(r, fpath, None)
            elif impname is not None:
                name = impname.split('.')[-1]
                file_globals_guess[impname][name] = None
                self.addGlobal(name, impname, None)

        src = self.absSrcFile.split(os.path.sep)
        dirname = os.path.dirname(self.absSrcFile)
        re_class = re.compile('public.*\s+class\s+(\w+)')
        re_enum = re.compile('public.*\s+enum\s+(\w+)')
        re_interface = re.compile('public.*\s+interface\s+(\w+)')
        for f in os.listdir(dirname):
            if f.endswith('.java'):
                fpath = os.path.join(dirname, f)
                handleFile(fpath)
        imports = []
        if cu.imports:
            imports = [i for i in cu.imports]
        for imp in imports:
            name = imp.name
            wildcard = imp.wildcard
            imp = name.split('.')
            while not imp[0] in src:
                imp.pop(0)
                if not imp:
                    break
            if not imp:
                continue
            fpath = src[:src.index(imp[0])] + imp
            fpath = "%s.java" % os.path.sep.join(fpath)
            handleFile(fpath)
        return

    def mapImport(self, name):
        base = self.opts.java_base
        if base:
            if name.startswith(base):
                name = name[len(base):]
                if name[0] == '.':
                    name = name[1:]
        return name

    def addImport(self, node, name, names):
        name = self.mapImport(name)
        node.nodes.append(ast.From(name, names, 0, None))

    def createImports(self, node, cu):
        unresolved = self.globals_ref.keys()
        files = {}
        for u in unresolved:
            fpath = self.globals[u][0]
            if isinstance(fpath, basestring):
                if not fpath in files:
                    files[fpath] = {}
                files[fpath][u] = 1
            else:
                found = False
                for fpath in file_globals:
                    if u in file_globals[fpath]:
                        if not fpath in files:
                            files[fpath] = {}
                        files[fpath][u] = 1
                        found = True
                if not found: print 'Could not resolve import for', u
        if self.package_name is None:
            pkg = []
        else:
            pkg = self.package_name.split('.')
        for fpath in files:
            names = files[fpath].keys()
            names.sort()
            name = fpath.split(os.path.sep)
            if name[-len(pkg)-1:-1] == pkg:
                name = '.'.join(name[-len(pkg)-1:])
                name = name[:-5]
                self.addImport(node, name, names)
            else:
                name = '.'.join(name)
                name = name[:-5]
                for imp in cu.imports:
                    if name.endswith(imp.name):
                        self.addImport(node, imp.name, names)

    def mapInitizer(self, init):
        s = str(init)
        if s == 'null':
            return ast.Name('None')
        return s

    def getOperatorExpr(self, e, op, left, right):
        if op is None:
            return None
        if left is None:
            left_expr = None
        else:
            left_expr = self.dispatch(left)
            assert left_expr is not None
        if right is None:
            right_expr = None
        else:
            right_expr = self.dispatch(right)
            assert right_expr is not None
        try:
            if right is None:
                if op == '-':
                    return ast.UnarySub(left_expr)
                if op == '!':
                    return ast.Not(left_expr)
                if op == '~':
                    return ast.Invert(left_expr)
                if op == '--':
                    args = [left_expr]
                    fname = 'PREDEC'
                    self.addJavaLib(fname)
                    return self.libCallGlobalsAndLocals(fname, args)
                    return ast.AugAssign(left_expr, '-=', ast.Const(1))
                if op == '++':
                    args = [left_expr]
                    fname = 'PREINC'
                    self.addJavaLib(fname)
                    return self.libCallGlobalsAndLocals(fname, args)
                    return ast.AugAssign(left_expr, '+=', ast.Const(1))
            if left is not None and right is not None:
                if op == '+':
                    return ast.Add((left_expr, right_expr))
                if op == '-':
                    return ast.Sub((left_expr, right_expr))
                if op == '*':
                    return ast.Mul((left_expr, right_expr))
                if op == '/':
                    return ast.Div((left_expr, right_expr))
                if op == '%':
                    return ast.Mod((left_expr, right_expr))
                if op == '&&':
                    return ast.And([left_expr, right_expr])
                if op == '||':
                    return ast.Or([left_expr, right_expr])
                if op == '&':
                    return ast.Bitand((left_expr, right_expr))
                if op == '|':
                    return ast.Bitor((left_expr, right_expr))
                if op == '^':
                    return ast.Bitxor([left_expr, right_expr])
                if op == '<<':
                    return ast.LeftShift((left_expr, right_expr))
                if op == '>>':
                    return ast.RightShift((left_expr, right_expr))
                if op == '>>>':
                    # -5>>>1 = -3&0x7FFFFFFF (int)
                    fname = 'URSHIFT'
                    self.addJavaLib(fname)
                    return self.libCallGlobalsAndLocals(fname, args)
            compare = None
            if op == '==':
                compare = '=='
                if isinstance(right, jast.Literal) and right.type == 'null':
                    compare = 'is'
                elif isinstance(left, jast.Literal) and left.type == 'null':
                    compare = 'is'
            elif op == '!=':
                compare = '!='
                if isinstance(right, jast.Literal) and right.type == 'null':
                    compare = 'is not'
                elif isinstance(left, jast.Literal) and left.type == 'null':
                    compare = 'is not'
            else:
                compare = op
            if compare is not None:
                return ast.Compare(
                    left_expr,
                    [(compare, right_expr)],
                )
        except:
            sys.stdout.write("%s\n" % ' '.join([repr(s) for s in [
                e.lineno, e, op, left_expr, right_expr,
            ]]))
            raise
        raise AstError(e, "getOperatorExpr '%s'" % op)
    
    def flatten_stmt(self, node, add_pass=False):
        def flatten(node):
            nodes = []
            comment_node = node
            for n in node.nodes:
                if isinstance(n, Type):
                    raise TypeError(n)
                if isinstance(n, ast.Stmt):
                    n = flatten(n)
                    #if n.comments:
                    #    comment_node.comments += n.comments
                    nodes += n.nodes
                elif isinstance(n, ast.EmptyNode):
                    if n.comments:
                        nodes.append(n)
                elif isinstance(n, ast.Pass):
                    if n.comments:
                        nn = ast.EmptyNode()
                        nn.comments = n.comments
                        nodes.append(nn)
                elif isinstance(n, ast.CallFunc):
                    if isinstance(n.node, ast.Name):
                        name = n.node.name
                        if name in ['POSTINC', 'POSTDEC', 'PREINC', 'PREDEC']:
                            if name[-3:] == 'INC':
                                op = '+='
                            else:
                                op = '-='
                            if isinstance(n.args[2], ast.Const):
                                varname = n.args[2].value
                                left_expr = ast.Name(varname)
                            else:
                                left_expr = n.args[2]
                            n = ast.AugAssign(left_expr, op, ast.Const(1))
                    nodes.append(n)
                else:
                    nodes.append(n)
            node.nodes[:] = nodes
            return node
        node = flatten(node)
        if add_pass and len(node.nodes) == 0:
            node.nodes.append(ast.Pass())
        return node

    def stmt(self, nodes, add_pass=False):
        node = ast.Stmt([])
        if nodes is not None:
            if not isinstance(nodes, list):
                print nodes
                print e.lineno
            for n in nodes:
                if isinstance(n, ast.Node):
                    node.nodes.append(n)
                elif not isinstance(n, jast.TypeNode):
                    node.nodes.append(self.dispatch(n))
        self.flatten_stmt(node, add_pass)
        return node

    def checkLabel(self, nodes):
        if len(self.label_stack) == 0:
            return
        node = ast.If(
            [(
                ast.Compare(
                    ast.Name(self.label_stack[-1]),
                    [('==', ast.Const('c'))],
                ),
                ast.Continue(),
            )],
            None,
        )
        if len(self.label_stack) == 1:
            node.tests.append((
                ast.Compare(
                    ast.Name(self.label_stack[-1]),
                    [('==', ast.Const('b'))],
                ),
                ast.Break(),
            ))
        else:
            node.else_ = ast.Break()
            stmt = ast.Stmt([node])
            node = ast.Or(
                [ast.Name(i) for i in self.label_stack],
            )
            node = ast.If([(node, stmt)], None)
        nodes.append(node)

    def dispatch(self, v):
        method = v.__class__.__name__.split('.')[-1]
        method = 'visit%s' % method
        node = getattr(self, method)(v)
        if not self.analysing:
            if isinstance(node, AnonymousClass):
                self.anon_count = getattr(self, 'anon_count', 0)
                if self.anon_count >= 1000:
                    raise AstError(v, "AnonymousClass")
                self.anon_count += 1
                return node
            if isinstance(node, Type):
                self.type_count = getattr(self, 'type_count', 0)
                if self.type_count >= 10000:
                    raise AstError(v, "Type")
                self.type_count += 1
                return node
        if isinstance(node, list):
            raise AstError(v, "node is list %s: %s" % (method, v))
        if node is None:
            raise AstError(v, "node is None %s: %s" % (method, v))
        if isinstance(node, ast.Node):
            node = self.node(v, node)
        if isinstance(node, ast.Stmt):
            self.flatten_stmt(node)
        return node

    def dispatch_list(self, src):
        dst = []
        for v in src:
            v = self.dispatch(v)
            if isinstance(v, Type):
                continue
            #if isinstance(v, basestring):
            #    v = ast.Name(v)
            dst.append(v)
        return dst
     
    def scoped(self, name):
        def pynode(names):
            node = names.pop(0)
            if isinstance(node, basestring):
                node = ast.Name(node)
            if len(names) > 0:
                node = ast.Getattr(node, names.pop(0))
                while len(names) > 0:
                    node = ast.Getattr(node, names.pop(0))
            return node
        if name in self.type_map:
            return pynode([self.mapType(name)])
        names = name.split('.')
        if (
            names[0] in self.scoped_ignore or
            self.hasLocal(names[0]) or
            self.hasGlobal(names[0])
        ):
            self.block_self_scope = False
            return pynode(names)
        if  self.block_self_scope:
            self.block_self_scope = False
            return pynode(names)
        if self.getClassDepth() <= 2 and len(self.locals) <= 2:
            return pynode([ast.CallFunc(
                ast.Name('super'),
                [ast.Name(self.getClassName(self.getClassDepth() - 1))],
            )] + names)
        return pynode(['self'] + names)

    def node(self, java_node, py_node):
        if java_node.comments:
            # Track the nodes done, in case we vitit this multiple times
            if not hasattr(py_node, 'comment_nodes'):
                py_node.comment_nodes = []
            if java_node not in py_node.comment_nodes:
                py_node.comment_nodes.append(java_node)
                comments = java_node.comments
                comments = [self.parseComment(c) for c in comments]
                if py_node.comments is None:
                    py_node.comments = comments
                else:
                    py_node.comments += comments
        py_node.lineno = java_node.lineno
        return py_node

    def libCallGlobalsAndLocals(self, name, arguments):
        args = [
            ast.CallFunc(ast.Name('globals'), []),
            ast.CallFunc(ast.Name('locals'), []),
        ] + arguments
        return ast.CallFunc(ast.Name(name), args)

    def methodMultiDeclElse(self, minargs, maxargs):
        self.addJavaLib('ARGERROR')
        return self.stmt([
            ast.Raise(
                ast.CallFunc(
                    ast.Name('ARGERROR'), 
                    [ast.Const(minargs), ast.Const(maxargs)],
                ),
                None,
                None,
            )],
        )

    def analyseMethodDefs(self, name):
        method = self.getMethod(name)
        #if method['count'] <= 1:
        #    return method
        method_nargs = method['nargs'].keys()
        method_nargs.sort()
        method_nargs.reverse()
        nothis_methods = []
        base = None
        generic_args = False
        args_mismatch = []
        swapped_constants = []
        swapped_arg_names = []
        segmented_defaults = []
        this_inits = {}
        segmented_inits = False
        multi_parents = False

        def setDefaults(idx, defaults):
            defaults = defaults[:]
            dst = base['defaults']
            while defaults:
                v = defaults.pop(0)
                if dst[idx] is None:
                    dst[idx] = v
                elif v == dst[idx]:
                    pass
                else:
                    generic_args = True
                    #print 'generic_args 1'
                    swapped_arg_names.append(v)
                idx += 1

        def argsMatch(left, right, n):
            while n:
                n -= 1
                if left[n] != right[n]:
                    return False
            return True
        
        def findParent(child):
            if not 'childs' in child:
                child['childs'] = []
            if 'parent' in child:
                return child['parent']
            if child.get('is_this') is None:
                child['parent'] = None
                return None
            if child['java_init'] is None:
                nargs = 0
            else:
                nargs = len(child['java_init'])
            candidates = method['nargs'][nargs]
            parent = None
            if len(candidates) == 1:
                parent = candidates[0]
            else:
                parent = candidates
            child['parent'] = parent
            for p in candidates:
                if not 'childs' in p:
                    p['childs'] = []
                p['childs'].append(child)
            return parent

        method_nargs = method['nargs'].keys()
        method_nargs.sort()
        minargs = method_nargs[0]
        maxargs = method_nargs[-1]
        method_nargs.reverse()
        for nargs in method_nargs:
            this_inits[nargs] = []
            methlist = method['nargs'][nargs]
            for m in methlist:
                java_args = m['java_args']
                java_init = m['java_init']
                py_init = m['py_init']
                is_this = m.get('is_this')
                parent = findParent(m)
                if isinstance(parent, list):
                    generic_args = True
                    #print 'generic_args 2'
                    multi_parents = True
                if base is None:
                    base = m
                else:
                    if not argsMatch(java_args,  base['java_args'], nargs):
                        generic_args = True
                        #print 'generic_args 3'
                        args_mismatch.append(m)
                        m['args_mismatch'] = True
                if is_this is None:
                    nothis_methods.append(m)
                    continue
                # Now we have a method definition with this(...)
                if py_init is None or len(py_init) == 0:
                    segments = []
                else:
                    last = py_init[0]
                    segments = [[last]]
                    for d in py_init[1:]:
                        if isinstance(last, ast.Const):
                            if isinstance(d, ast.Const):
                                segments[-1].append(d)
                            else:
                                segments.append([d])
                        else:
                            if isinstance(d, ast.Const):
                                segments.append([d])
                            else:
                                segments[-1].append(d)
                if len(segments) == 0:
                    # No arguments in this() call
                    pass
                elif len(segments) == 1:
                    if isinstance(segments[0][0], ast.Const):
                        # All py_init are constants
                        setDefaults(0, segments[0])
                    else:
                        # Either names or other initialisations
                        if not 'this_inits' in m:
                            m['this_inits'] = []
                        m['this_inits'] = [i for i in segments[0]]
                        this_inits[nargs].append(m['this_inits'])
                elif len(segments) == 2:
                    if not isinstance(segments[0][0], ast.Const):
                        # Second half is constants
                        setDefaults(len(segments[0]), segments[1])
                    else:
                        # First half is constants
                        generic_args = True
                        #print 'generic_args 4'
                        swapped_constants.append(m)
                        m['swapped_constants'] = segments
                else:
                    # Segmented defaults
                    generic_args = True
                    #print 'generic_args 5'
                    segmented_defaults.append(m)
                    m['segmented_defaults'] = segments

        for i in this_inits.itervalues():
            if len(i) > 1:
                generic_args = True
                #print 'generic_args 6'
                segmented_inits = True

        if minargs != 0 or maxargs != 0:
            if minargs != maxargs:
                generic_args = True
        # Reorder nothis_methods from most-to-least to 
        # least-to-most arguments
        nothis_methods.reverse()
        props = dict(
            nothis_methods=nothis_methods,
            generic_args=generic_args,
            args_mismatch=args_mismatch,
            swapped_constants=swapped_constants,
            swapped_arg_names=swapped_arg_names,
            segmented_defaults=segmented_defaults,
            segmented_inits=segmented_inits,
            multi_parents=multi_parents,
        )

        tmpvars = self.tmpvars[-1]
        self.tmpvars[-1] = {}
        if not generic_args:
            self.getNormalPyMethod(
                name,
                method, 
                base, 
                props,
            )
        else:
            self.getGenericPyMethod(
                name,
                method, 
                base, 
                props,
            )
        self.tmpvars[-1] = tmpvars

        method = self.getMethod(name)
        method['props'] = props
        return method

    def getNormalPyMethod(self, name, method, base, props):

        def getBodyNodes(m):
            java_body = m['java_body']
            if java_body is None:
                return []
            ign_expl_constr = self.ign_expl_constr
            self.ign_expl_constr = True
            py_body = self.stmt(java_body)
            self.ign_expl_constr = ign_expl_constr
            return py_body

        def sortedChilds(childs):
            # Sort childs on number of arguments
            # Least to most arguments
            childs = [(len(m['java_args']), m) for m in childs]
            childs.sort()
            return [m for n, m in childs]

        def getChildInits(methods):
            if len(methods) == 0:
                return []
            have_init = False
            ifnode = ast.If([], None)
            for m in methods:
                if not m.get('this_inits'):
                    continue
                have_init = True
                init_nodes = []
                idx = -1
                for src in m['this_inits']:
                    idx += 1
                    if isinstance(src, ast.Name):
                        continue
                    dst = base['java_args'][idx]
                    dst = dst.name
                    if isinstance(src, AnonymousClass):
                        raise AstError(e, "AnonymousClass")
                    init_nodes.append(
                        ast.Assign(
                            [ast.AssName(dst, 'OP_ASSIGN')],
                            src,
                        ),
                    )
                nargs = len(m['java_args'])
                if nargs >= len(base['java_args']):
                    ifnode.else_ = self.stmt(init_nodes)
                else:
                    arg = base['java_args'][nargs]
                    arg = arg.name
                    ifnode.tests.append((
                        ast.Compare(
                            ast.Name(arg),
                            [('is', ast.Const(None))],
                        ),
                        self.stmt(init_nodes, True),
                    ))
            if not have_init:
                return []
            if not ifnode.tests:
                if ifnode.else_:
                    return ifnode.else_.nodes
                assert False
            return [ifnode]

        def getNodes(methods):
            if len(methods) == 0:
                return []
            m = methods[0]
            if len(methods) == 1:
                parent = m['parent']
                childs = sortedChilds(m['childs'])
                py_body = getChildInits(childs)
                nodes = [ast.Pass()] + py_body
                py_body.append(getBodyNodes(m))
                if parent is None:
                    nodes = py_body + getNodes(childs)
                else:
                    ifnode = ast.If([], None)
                    nargs = len(m['java_args'])
                    parent_nargs = len(parent['java_args'])
                    cmpop = 'is'
                    if parent_nargs > len(base['java_args']):
                        cmpop = 'is not'
                        arg = base['java_args'][parent_nargs - 1]
                        default = base['defaults'][parent_nargs - 1]
                    elif parent_nargs == 0:
                        arg = base['java_args'][nargs - 1]
                        default = base['defaults'][nargs - 1]
                    else:
                        arg = base['java_args'][nargs - 1]
                        default = base['defaults'][nargs - 1]
                    arg = arg.name
                    if default is not None:
                        py_body = [ast.Assign(
                            [ast.AssName(arg, 'OP_ASSIGN')],
                            default,
                        )] + py_body
                    stmt = self.stmt(py_body)
                    ifnode.tests.append((
                        ast.Compare(
                            ast.Name(arg),
                            [(cmpop, ast.Const(None))],
                        ),
                        stmt,
                    ))
                    py_body += getNodes(sortedChilds(m['childs']))
                    self.flatten_stmt(stmt, True),
                    nodes = [ifnode]
            else:
                ifnode = ast.If([], None)
                comment_node = ifnode
                for m in methods:
                    childs = sortedChilds(m['childs'])
                    py_body = getChildInits(childs)
                    nodes = [comment_node] + py_body
                    py_body.append(getBodyNodes(m))
                    nargs = len(m['java_args'])
                    stmt = self.stmt(py_body)
                    if nargs >= len(base['java_args']):
                        ifnode.else_ = stmt
                    else:
                        arg = base['java_args'][nargs]
                        arg = arg.name
                        ifnode.tests.append((
                            ast.Compare(
                                ast.Name(arg),
                                [('is', ast.Const(None))],
                            ),
                            stmt,
                        ))
                    py_body += getNodes(childs)
                    self.flatten_stmt(stmt, True)
                    comment_node = py_body[-1]
                self.flatten_stmt(ifnode.else_)
                if len(ifnode.else_.nodes) == 0:
                    ifnode.else_ = None
                if not ifnode.tests:
                    if ifnode.else_:
                        return ifnode.else_.nodes
                    assert False
                nodes = [ifnode]
            return nodes

        method_nargs = method['nargs'].keys()
        method_nargs.sort()
        minargs = method_nargs[0]
        maxargs = method_nargs[-1]

        defaults = [ast.Const(None) for i in base['defaults'] if i is not None]
        #while defaults and defaults[0] is None:
        #    defaults.pop(0)
        # Transform bodies to python
        # From least to most args
        # Parent to childs
        nothis_methods = props['nothis_methods']
        nodes = getNodes(nothis_methods)

        n = len(base['java_args']) - len(defaults)
        while n > minargs:
            n -= 1
            defaults.insert(0, ast.Const(None))
        node = self.stmt(nodes, True)
        base['py_body'] = node
        base['defaults'] = defaults
        self.setMethod(name)
        self.addMethod(name, **base)

    def getGenericPyMethod(self, name, method, base, props):

        def getBodyNodes(parameters, java_body):
            # Get python ast for java_body
            if java_body is None:
                 return []
            self.descend()
            # Collect local variables
            self.pushState()
            self.analysing += 1
            try:
                [self.dispatch(b) for b in java_body]
                init_args = None
            except AstInitArgs, a:
                pass
            for p in parameters:
                self.addLocal(p.name, p, None)
            state = self.popState()
            # Now for real
            self.locals = state.locals
            ign_expl_constr = self.ign_expl_constr
            self.ign_expl_constr = False
            raise_expl_constr = self.raise_expl_constr
            self.raise_expl_constr = False
            py_body = self.dispatch_list(java_body)
            self.raise_expl_constr = raise_expl_constr
            self.ign_expl_constr = ign_expl_constr
            self.ascend()
            nodes = py_body
            #while nodes and isinstance(nodes[0], ast.Pass):
            #    nodes.pop(0)
            return py_body

        def getNodes(m):
            params = m['java_args']
            if params:
                parameters = self.dispatch_list(params)
            else:
                parameters = []
            if not self.opts.as_module or self.getClassDepth() > 2:
                parameters.insert(0, ast.Name('self'))

            nodes = []
            args = [ast.AssName(a.name, 'OP_ASSIGN') for a in parameters[1:]]
            if len(args) > 0:
                nodes.append(
                    ast.Assign([ast.AssTuple(args)], ast.Name(argsvar)),
                )

            # Get python ast for java_body
            nodes += getBodyNodes(parameters, m['java_body'])
            return nodes

        def argsDiff(nargs, methlist):
            methlist = methlist[:]
            methtypes = []
            for m in methlist:
                methtypes.append([])
                for a in m['java_args']:
                    a = self.dispatch(a.type)
                    if hasattr(a, 'name'):
                        a = a.name
                    t = self.mapType(a)
                    methtypes[-1].append(t)
                methtypes[-1].append(m)
            methtypes.sort()
            start = -1
            while start < nargs:
                start += 1
                left = methtypes[0][start]
                match = True
                for right in methtypes[1:]:
                    right = right[start]
                    if left != right:
                        match = False
                        break
                if not match:
                    break
            diffs = {}
            for mt in methtypes:
                if len(mt) == 1:
                    continue
                t, m = mt[start], mt[-1]
                if not isinstance(t, basestring):
                    continue
                if not t in diffs:
                    diffs[t] = []
                diffs[t].append(m)
            return start, diffs
        
        def argsDiffNodes(nargs, methlist):
            if nargs == 0:
                return []
            if len(methlist) == 0:
                return []
            if len(methlist) == 1:
                return getNodes(methlist[0])
            start, diffs = argsDiff(nargs, methlist)
            types = diffs.keys()
            if len(types) == 0:
                # FIXME: Tree.java line 936 anon class def missing
                nodes = []
                for m in methlist:
                    nodes += getNodes(m)
                return nodes
                return getNodes(methlist[0])
            types.sort()
            nodes = []
            ifnode = ast.If([], self.stmt(nodes))
            nodes += argsDiffNodes(nargs, diffs[types[-1]])
            for t in types[:-1]:
                methlist = diffs[t]
                nodes = argsDiffNodes(nargs, methlist)
                arg = ast.Subscript(
                    ast.Name(argsvar),
                    'OP_APPLY',
                    [ast.Const(start)],
                )
                ifnode.tests.append((
                    ast.CallFunc(
                        ast.Name('isinstance'),
                        [arg, ast.Name(t)],
                    ),
                    self.stmt(nodes, True),
                ))
            self.flatten_stmt(ifnode.else_)
            if len(ifnode.else_.nodes) == 0:
                ifnode.else_ = None
            return [ifnode]

        method_nargs = method['nargs'].keys()
        method_nargs.sort()
        minargs = method_nargs[0]
        maxargs = method_nargs[-1]
        nargs = minargs - 1
        nodes = []
        argsvar, init = self.getTmpVar(ast.Name('args'))
        nodes.append(init)
        nargsvar, init = self.getTmpVar(
            ast.CallFunc(ast.Name('len'), [ast.Name('args')]),
        )
        nodes.append(init)
        argif_node = ast.If(
            [],
            self.methodMultiDeclElse(minargs, maxargs),
        )
        nodes.append(argif_node)
        while nargs < maxargs:
            nargs += 1
            if not nargs in method_nargs:
                continue
            test = [
                ast.Compare(
                    ast.Name(nargsvar),
                    [('==', ast.Const(nargs))],
                ),
                self.stmt([]),
            ]
            argif_node.tests.append(test)

            methlist = method['nargs'][nargs]
            if len(methlist) == 1:
                m = methlist[0]
                test[1].nodes += getNodes(m)
                continue
            test[1].nodes += argsDiffNodes(nargs, methlist)
        node = self.stmt(nodes, True)
        base['defaults'] = []
        base['py_body'] = node
        self.setMethod(name)
        self.addMethod(name, **base)

    def methodAppendDoc(self, name, doc):
        if doc is not None:
            node, jtype = self.getLocal(name)
            node.doc = "%s\n---\n%s" % (
                node.doc,
                self.parseComment(doc),
            )
        return ast.Pass()

    def methodDeclaration(self, d, name, doc, params, body):
        if self.analysing:
            ign_expl_constr = self.ign_expl_constr
            self.ign_expl_constr = False
            self.addLocal(name, None, d.typeParameters)
            self.descend()
            for p in params:
                self.addLocal(p.name, None, p.type)
            try:
                depth = self.getStackDepth()
                if body is not None:
                    [self.dispatch(b) for b in body]
                self.addMethod(
                    name, 
                    decl_ast=d,
                    java_args=params,
                    java_body=body,
                )
            except AstInitArgs, a:
                self.setStackDepth(depth)
                self.addMethod(
                    name,
                    decl_ast=d,
                    java_args=params,
                    java_init=a.java_init,
                    py_init=a.py_init,
                    java_body=body,
                    excpl_constr=a.node,
                    is_this=a.is_this,
                )
            self.ascend()
            self.ign_expl_constr = ign_expl_constr
            return ast.Pass()
        defaults = None
        method = self.getMethod(name)
        if 'props' in method:
            self.methodAppendDoc(name, doc)
            return ast.Pass()
        self.descend()
        for p in params:
            self.addLocal(p.name, None, p.type)
        method = self.analyseMethodDefs(name)
        self.ascend()
        generic_args = method['props']['generic_args']
        assert len(method['nargs']) == 1
        for n in method['nargs']:
            # Should be just one :-)
            m = method['nargs'][n][0]
            params = m['java_args']
            defaults = m['defaults']
            code = m['py_body']
        if params:
            parameters = self.dispatch_list(params)
        else:
            parameters = []
        if not self.opts.as_module or self.getClassDepth() > 2:
            parameters.insert(0, ast.Name('self'))
        flags = 0
        if generic_args:
            flags = ast.CO_VARARGS
            parameters = parameters[:1]
            parameters.append(ast.Name('args'))
        elif len(params) > 0 and '...' in params[-1].type_modifier:
            flags = ast.CO_VARARGS
        node = ast.Function(
            None, # decorators
            name,
            parameters,
            defaults,
            flags,
            self.parseComment(doc),
            code,
        )
        method['node'] = node
        self.addLocal(name, node, None)
        return node

    def anonClass(self, ac, dst=None):
        if dst is None:
            anonvar = ac.cls_node.name
        else:
            anonvar = dst.name
        cls = ac.cls_node
        cls.name = anonvar
        stmt = self.stmt([])
        if ac.this_suffixes:
            for jname, tmpname, names in ac.this_suffixes:
                assert len(names) == 2
                stmt.nodes.append(ast.Assign(
                    [ast.AssName(tmpname, 'OP_ASSIGN')],
                    ast.Name('self'),
                ))
        stmt.nodes.append(cls)
        name = ast.Name(anonvar)
        stmt.nodes.append(
            ast.Assign(
                [ast.AssName(anonvar, 'OP_ASSIGN')],
                self.fixCallFunc(
                    ac.java_node,
                    name,
                ),
            ),
        )
        return (stmt, name)

    def fixCallFunc(self, e, node, arguments=None):
        node_ast = ast.CallFunc
        if isinstance(node, basestring):
            node = self.scoped(node)
        elif isinstance(node, ast.Name):
            n = node
            node = self.scoped(node.name)
            node.comments = n.comments
        elif isinstance(node, AnonymousClass):
            raise AstError(e, "AnonymousClass")
        elif isinstance(node, ast.Getattr):
            attrname = node.attrname
            if isinstance(attrname, ast.Name):
                attrname = attrname.name
            if attrname in self.method_map:
                name, replace, pymeth = self.method_map[attrname]
                if name is None:
                    return node.expr
                if replace:
                    node.attrname = name
                else:
                    if name == '[]':
                        if len(e.arguments) == 1:
                            node_ast = ast.Subscript
                            node = node.expr
                    elif name == '==':
                        assert len(e.arguments) == 1
                        node_ast = ast.Compare
                        node = node.expr
                    elif name == 'str' and len(e.arguments) == 1:
                        node = ast.Name(name)
                    else:
                        if not e.arguments:
                            node = ast.CallFunc(
                                ast.Name(name),
                                [node.expr],
                            )
                            node.comments = e.comments
                            return node
                        else:
                            node = ast.Name(name)
        if arguments is None:
            arguments = e.arguments
        args = []
        stmt = self.stmt([])
        for a in arguments:
            if not isinstance(a, ast.Node):
                a = self.dispatch(a)
            if isinstance(a, basestring):
                a = ast.Name(a)
            elif isinstance(a, AnonymousClass):
                cls, a = self.anonClass(a)
                stmt.nodes.append(cls)
            elif isinstance(a, Type):
                raise AstError(e, "Type")
            args.append(a)
        if node_ast is ast.Subscript:
            node = node_ast(node, 'OP_APPLY', args)
        elif node_ast is ast.Compare:
            op = '=='
            if (
                len(args) == 1
                and isinstance(args[0], ast.Const)
                and args[0].value is None
            ):
                op = 'is'
            node = node_ast(node, [(op, a) for a in args])
        else:
            node = node_ast(node, args)
        if len(stmt.nodes) > 0:
            stmt.nodes.append(node)
            self.flatten_stmt(stmt)
            node = stmt
        return node

    # TOP

    def visitAdditiveExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitAdditiveExpr %s'" % op)

    def visitAndExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitAndExpr %s'" % op)

    def visitAnnotationType(self, e):
        return ast.EmptyNode()

    def visitArrayAccess(self, e):
        return ast.Subscript(
            self.dispatch(e.name),
            'OP_APPLY',
            self.dispatch_list(e.array),
        )

    def visitArrayCreator(self, e):
        if e.initializer is not None:
            if isinstance(e.initializer, list):
                nodes = self.dispatch_list(e.initializer)
            else:
                nodes = [self.dispatch(e.initializer)]
            return ast.List(nodes)
        return ast.EmptyNode()
        raise NotImplemented

    def visitAssert(self, e):
        test = self.dispatch(e.test)
        value = e.value
        if value is not None:
            value = self.dispatch(e.value)
        return ast.Assert(test, value)

    def visitAssignExpr(self, e):
        op = e.op
        dst = self.dispatch(e.left)
        value = self.dispatch(e.right)
        stmt = None
        if isinstance(value, AnonymousClass):
            stmt, value = self.anonClass(value)
        elif isinstance(value, ast.Stmt):
            stmt = value
            if isinstance(value.nodes[0], ast.Class):
                # Anonymous class defined
                cls = value.nodes[0]
                value = ast.Name(cls.name)
            else:
                raise AstError(e, "Statement")
        augassign = True
        if op == '=':
            op = 'OP_ASSIGN'
            augassign = False
        elif op == '>>>=':
            op = 'OP_ASSIGN'
            augassign = False
            fname = 'URSHIFT'
            self.addJavaLib(fname)
            value = ast.CallFunc(
                ast.Name(fname),
                [value],
            )
        if augassign:
            node = ast.AugAssign(dst, op, value)
            if stmt:
                stmt.nodes.append(node)
                return stmt
            return node
        nodes = []
        if isinstance(dst, ast.Name):
            nodes.append(ast.AssName(dst.name, op))
        elif isinstance(dst, ast.Getattr):
            if isinstance(dst.expr, AnonymousClass):
                raise AstError(e, "AnonymousClass")
            nodes.append(ast.AssAttr(dst.expr, dst.attrname, op))
        elif isinstance(dst, ast.Subscript):
            dst.flags = op
            nodes.append(dst)
        if nodes:
            if isinstance(value, AnonymousClass):
                raise AstError(e, "AnonymousClass")
            node = ast.Assign(nodes, value)
            if stmt:
                stmt.nodes.append(node)
                return stmt
            return node
        raise AstError(e, "visitAssignExpr '%s'" % dst)

    def visitBitAndExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitBitAndExpr %s'" % op)

    def visitBitOrExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitBitOrExpr %s'" % op)

    def visitBitXorExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitBitXorExpr %s'" % op)

    def visitBreak(self, e):
        if e.label is None:
            return ast.Break()
        nodes = []
        nodes.append(ast.Assign(
                [ast.AssName(e.label, 'OP_ASSIGN')],
                ast.Const('b'),
            )
        )
        nodes.append(ast.Break())
        return self.stmt(nodes)

    def visitCallFunc(self, e):
        node = self.dispatch(e.node)
        return self.fixCallFunc(e, node)

    def visitCastExpr(self, e):
        return self.dispatch(e.node)

    def visitClass(self, e):
        if not self.opts.as_module:
            if self.getClassDepth() == 1:
                self.addGlobal(e.name, None, e)
        elif self.getClassDepth() == 2:
            self.addGlobal(e.name, None, e)
        self.pushClassName(e.name)
        self.pushState()
        self.analysing += 1
        for n in e.members:
            self.dispatch(n)
        state = self.popState()
        self.locals = state.locals
        if e.extends is None:
            bases = ['object']
        else:
            # Interface can have multiple extends
            bases = []
            if isinstance(e.extends, list):
                extends = e.extends
            else:
                extends = [e.extends]
            for extend in extends:
                base = self.dispatch(extend)
                assert isinstance(base, Type)
                base = self.mapType(base.name)
                bases.append(base)
                self.hasGlobal(base)
        if hasattr(e, 'implements') and e.implements is not None:
            for impl in e.implements:
                if isinstance(impl.type, jast.ClassOrInterfaceType):
                    for t in impl.type.types:
                        name = t[0]
                        self.hasGlobal(name)
                        bases.append(name)
                else:
                    self.hasGlobal(impl.name)
                    bases.append(impl.name)
                    assert False
        doc = self.parseComment(e.doc)
        stmt = self.stmt([])
        class_node = ast.Class(
            e.name,
            bases,
            doc,
            stmt,
            e.lineno
        )
        for n in e.members:
            try:
                depth = self.getStackDepth()
                node = self.dispatch(n)
                if isinstance(node, list):
                    stmt.nodes += node
                elif isinstance(node, ast.Pass):
                    if node.comments:
                        stmt.nodes.append(node)
                else:
                    stmt.nodes.append(node)
            except IgnoreMethod:
                self.setStackDepth(depth)
        self.flatten_stmt(stmt, True)
        self.popClassName()
        if self.opts.as_module and self.getClassDepth() == 1:
            if doc:
                node = ast.EmptyNode()
                node.comments = [doc]
                stmt.nodes.insert(0, node)
            return stmt
        return class_node

    def visitClassBlock(self, e):
        node = self.stmt(self.dispatch_list(e.nodes))
        return self.flatten_stmt(node, True)

    def visitClassCreator(self, e):
        this_suffixes = self.this_suffixes
        self.this_suffixes = []
        analysing = self.analysing
        self.analysing = 1
        name, init = self.getTmpVar(None, 0)
        self.pushClassName(name)
        cls_type = self.dispatch(e.type)
        assert isinstance(cls_type, Type)
        cls_type = cls_type.name
        node = None
        if not e.nodes:
            node = self.fixCallFunc(
                e,
                ast.Name(cls_type),
            )
        body = self.stmt(e.nodes, False)
        self.analysing = 0
        body = self.stmt(e.nodes, False)
        self.popClassName()
        self.analysing = analysing
        _this_suffixes = self.this_suffixes
        self.this_suffixes = this_suffixes
        if node is not None:
            return node
        name, init = self.getTmpVar(None, 0)
        return AnonymousClass(
            ast.Class(name, [cls_type], None, body),
            e,
            _this_suffixes,
        )

    def visitClassOrInterfaceType(self, e):
        names = [t[0] for t in e.types]
        return Type('.'.join(names))

    def visitConditionalExpr(self, e):
        test = self.dispatch(e.test)
        then = self.dispatch(e.then)
        else_ = self.dispatch(e.else_)
        return ast.IfExp(test, then, else_)

    def visitContinue(self, e):
        if e.label is None:
            return ast.Continue()
        nodes = []
        nodes.append(ast.Assign(
                [ast.AssName(e.label, 'OP_ASSIGN')],
                ast.Const('c'),
            )
        )
        nodes.append(ast.Break())
        return self.stmt(nodes)


    def visitDoWhile(self, e):
        nodes = []
        statevar, init = self.getTmpVar(ast.Const(True))
        nodes.append(init)
        test = ast.Or([
            ast.Name(statevar),
            self.dispatch(e.test),
        ])
        body = self.stmt(e.nodes)
        init = ast.Assign(
            [ast.AssName(statevar, 'OP_ASSIGN')],
            ast.Const(False),
        )
        body.nodes.insert(0, init)
        node = ast.While(test, body, None)
        node.comment_nodes = [e]
        if e.comments:
            node.comments = [
                [self.parseComment(c) for c in e.comments[0]],
                [],
                [self.parseComment(c) for c in e.comments[1]],
            ]
        nodes = [node]
        self.checkLabel(nodes)
        return self.stmt(nodes)

    def visitEmptyNode(self, e):
        return ast.EmptyNode()

    def visitEnum(self, e):
        if self.getClassDepth() == 1:
            self.addGlobal(e.name, None, e)
        self.pushClassName(e.name)
        self.pushState()
        self.analysing += 1
        for n in e.members:
            self.dispatch(n)
        state = self.popState()
        self.locals = state.locals
        bases = ['object']
        doc = self.parseComment(e.doc)
        stmt = self.stmt([])
        class_node = ast.Class(
            e.name,
            bases,
            doc,
            stmt,
            e.lineno
        )
        for n in e.constants:
            node = self.dispatch(n)
            stmt.nodes.append(node)
        for n in e.members:
            try:
                depth = self.getStackDepth()
                node = self.dispatch(n)
                if isinstance(node, list):
                    stmt.nodes += node
                elif isinstance(node, ast.Pass):
                    if node.comments:
                        stmt.nodes.append(node)
                else:
                    stmt.nodes.append(node)
            except IgnoreMethod:
                self.setStackDepth(depth)
        self.flatten_stmt(stmt, True)
        self.popClassName()
        return class_node

    def visitEnumConstant(self, e):
        node = self.dispatch(e.name)
        name = e.name.name
        right = ast.Const(name)
        comments = None
        if len(e.nodes) > 0:
            self.warning(e.lineno, 'Cannot handle nodes in enum constants')
            if len(e.nodes) == 1 and isinstance(e.nodes[0], jast.Method):
                expr = e.nodes[0].body[0]
                if isinstance(expr, jast.Return):
                    right = self.dispatch(expr.expr)
            right.comments = ['FIXME: EnumConstant (line: %s)' % e.lineno]
        if not e.arguments:
            node = ast.Assign(
                [ast.AssName(name, 'OP_ASSIGN')],
                right,
            )
        else:
            print e.arguments
            args = self.dispatch_list(e.arguments)
            print args
            raise NotImplemented
        return node

    def visitEqualityExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitEqualityExpr %s'" % op)

    def visitFieldDeclaration(self, e):
        nodes = []
        for v in e.variables:
            name = v.name
            init = v.initializer
            if init is None:
                init = ast.Const(None)
            elif e.type.array and isinstance(init, list):
                init = self.dispatch_list(init)
                init = ast.List(init)
            else:
                if init is None:
                    init = ast.Const(None)
                else:
                    init = self.dispatch(init)
                    if isinstance(init, AnonymousClass):
                        cls, name = self.anonClass(init, v)
                        cls = init.cls_node
                        cls.name = v.name
                        nodes.append(cls)
                        continue
            if isinstance(init, AnonymousClass):
                raise AstError(e, "AnonymousClass")
            a = ast.Assign(
                    [ast.AssName(name, 'OP_ASSIGN')],
                    init,
                )
            nodes.append(a)
            self.addLocal(a.nodes[0].name, a.nodes[0], e.type)
        return self.stmt(nodes)

    def visitFor(self, e):
        nodes = []
        stmt = self.stmt(nodes)
        isfirstvar, init = self.getTmpVar(ast.Const(True))
        stmt.nodes.append(init)
        init = e.init
        if init is not None:
            init = self.stmt(e.init)
            for node in init.nodes:
                stmt.nodes.append(node)
        compare = e.expr
        if compare is None:
            compare = ast.Const(True)
        else:
            compare = self.dispatch(compare)
        update = e.update
        if update is not None:
            update = self.stmt(update, True)
        body = e.nodes
        if body is None:
            body = ast.Stmt([ast.Pass()])
        else:
            body = self.stmt(e.nodes, True)
        # Attach comments from block top to an empty node
        empty = ast.EmptyNode()
        if e.comments:
            empty.comments = [self.parseComment(c) for c in e.comments[0]]
        else:
            e.comments = None
        empty.comment_nodes = [e]
        # And insert that at the top of the body
        body.nodes.insert(0, empty)
        node = ast.While(
            ast.Const(True),
            self.stmt([
                ast.If(
                    [(
                        ast.Compare(ast.Name(isfirstvar), [('is', ast.Const(True))]),
                        self.stmt([
                            ast.Assign(
                                [ast.AssName(isfirstvar, 'OP_ASSIGN')],
                                ast.Const(False)),
                        ]),
                    )],
                    update,
                ),
                ast.If(
                    [(
                        compare,
                        ast.Pass(),
                    )],
                    ast.Break(),
                ),
                body,
            ]),
            None,
        )
        stmt.nodes.append(node)
        # Attach comments from block outer to an empty node
        empty = ast.EmptyNode()
        if e.comments:
            empty.comments = [self.parseComment(c) for c in e.comments[1]]
        else:
            e.comments = None
        empty.comment_nodes = [e]
        # And append that to the statements
        stmt.nodes.append(empty)
        self.checkLabel(stmt.nodes)
        return stmt

    def visitForEach(self, e):
        name = e.var[1].name
        self.addLocal(name, None, e.var[0])
        var = self.dispatch(e.var[1])
        test = self.dispatch(e.test)
        body = self.stmt(e.nodes)
        node = ast.For(
            ast.AssName(
                name,
                'OP_ASSIGN',
            ),
            test,
            body,
            None,
        )
        if e.comments:
            assert len(e.comments) == 2
            node.comments = [
                [self.parseComment(c) for c in e.comments[0]],
                [],
                [self.parseComment(c) for c in e.comments[1]],
            ]
        else:
            node.comments = None
        nodes = [node]
        self.checkLabel(nodes)
        return self.stmt(nodes)

    def visitIdentifier(self, e):
        if e.name == 'this':
            return ast.Name('self')
        if e.name == 'super':
            self.block_self_scope = True
            node = ast.CallFunc(
                ast.Name('super'),
                [ast.Name(self.getClassName()), ast.Name("self")],
            )
            return node
        name = self.scoped(e.name)
        return name

    def visitIf(self, e):
        test = self.dispatch(e.test)
        then = self.stmt(e.then, True)
        if e.else_ is None:
            else_ = None
        else:
            else_ = self.stmt(e.else_)
        node = ast.If(
            [(test, then)],
            else_,
        )
        # Change comments from flat list to
        # (test, then)+ else rest
        ntests = len(node.tests)
        node.comments = []
        node.comment_nodes = [e]
        comments = e.comments[:]
        while ntests > 0:
            ntests -= 1
            c1 = comments.pop(0)
            c1 = [self.parseComment(c) for c in c1]
            c2 = comments.pop(0)
            c2 = [self.parseComment(c) for c in c2]
            node.comments.append((c1, c2))
        while len(comments) > 0:
            c1 = comments.pop(0)
            c1 = [self.parseComment(c) for c in c1]
            node.comments.append(c1)
        return node

    def visitInstanceOfExpr(self, e):
        type_ = self.dispatch(e.type)
        assert isinstance(type_, Type)
        type_ = self.mapType(type_.name)
        return self.fixCallFunc(
            e,
            ast.Name("isinstance"),
            [e.node, ast.Name(type_)],
        )

    def visitInterface(self, e):
        return self.visitClass(e)

    def visitLabel(self, e):
        self.addLocal(e.name, None, None)
        nodes = []
        nodes.append(ast.Assign(
                [ast.AssName(e.name, 'OP_ASSIGN')],
                ast.Const(''),
            )
        )
        self.label_stack.append(e.name)
        nodes.append(self.dispatch(e.node))
        assert self.label_stack.pop() == e.name
        assert isinstance(nodes[-1], ast.Stmt)
        assert isinstance(nodes[-1].nodes[-1], ast.If)
        # Remove last label check
        #print nodes[-1].nodes[-1]
        #node = nodes[-1].nodes[-1].tests[0][0].expr
        #assert e.name == node.name
        node = nodes[-1].nodes[-1].tests[0][0]
        if isinstance(node, ast.Compare):
            nodes[-1].nodes.pop()
        return self.stmt(nodes)

    def visitLiteral(self, e):
        return ast.Const(e.value)

    def visitLocalVariableDeclaration(self, e):
        nodes = []
        for v in e.variables:
            name = v.name
            init = v.initializer
            if init is None:
                pass
            elif e.type.array and isinstance(init, list):
                init = self.dispatch_list(init)
                init = ast.List(init)
            else:
                if init is not None:
                    init = self.dispatch(init)
                    if isinstance(init, AnonymousClass):
                        cls, name = self.anonClass(init, v)
                        cls = init.cls_node
                        cls.name = v.name
                        nodes.append(cls)
                        continue
            if isinstance(init, AnonymousClass):
                raise AstError(e, "AnonymousClass")
            if init is None:
                self.addLocal(name, None, e.type)
            else:
                a = ast.Assign(
                        [ast.AssName(name, 'OP_ASSIGN')],
                        init,
                    )
                nodes.append(a)
                self.addLocal(name, a.nodes[0], e.type)
        return self.stmt(nodes)

    def visitMethod(self, e):
        if self.analysing > 1:
            return ast.Pass()
        if e.name == self.getClassName():
            e.name = '__init__'
        return self.methodDeclaration(
            e,
            e.name,
            e.doc,
            e.parameters,
            e.body,
        )

    def visitMultiplicativeExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitMultiplicativeExpr %s'" % op)

    def visitOrExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitOrExpr %s'" % op)

    def visitParameterDecl(self, e):
        return ast.Name(e.name)

    def visitParExpr(self, e):
        return self.dispatch(e.node)

    def visitPrimitiveType(self, e):
        return Type(e.name)

    def visitPostOperator(self, e):
        if e.op == '++':
            fname = 'POSTINC'
        else:
            fname = 'POSTDEC'
        self.addJavaLib(fname)
        args = [self.dispatch(e.node)]
        return self.libCallGlobalsAndLocals(fname, args)

    def visitQualifiedIdentifier(self, e):
        names = e.name.split('.')
        if names[0] == 'this':
            names[0] = 'self'
        if names[-1] == 'this':
            name = '_%s' % '_'.join(names)
            value = (e.name, name, names)
            if not value in self.this_suffixes:
                self.this_suffixes.append(value)
            return ast.Name(name)
        issuper = names[0] == 'super'
        if issuper:
            raise AstError(e, 'issuper')
            names.pop(0)
        node = self.scoped('.'.join(names))
        if issuper:
            node = self.fixCallFunc(
                e,
                ast.CallFunc(
                    ast.Name('super'),
                    [ast.Name(self.getClassName()), ast.Name("self")],
                ),
            )
        return node

    def visitRelationalExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitRelationalExpr %s'" % op)

    def visitReturn(self, e):
        if e.expr is None:
            return ast.Return(None)
        expr = self.dispatch(e.expr)
        if isinstance(expr, AnonymousClass):
            cls, name = self.anonClass(expr)
            cls.nodes.append(ast.Return(name))
            return cls
        return ast.Return(expr)

    def visitSelector(self, e):
        left = self.dispatch(e.left)
        self.block_self_scope = True
        right = self.dispatch(e.right)
        if isinstance(left, AnonymousClass):
            cls, name = self.anonClass(left)
            cls.nodes.append(ast.Getattr(name, right))
            return cls
        return ast.Getattr(left, right)

    def visitShiftExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.left, e.right)
        if expr is not None:
            return expr
        raise AstError(e, "visitShiftExpr %s'" % op)

    def visitSuper(self, e):
        args = e.arguments
        if args is None:
            args = []
        node = self.fixCallFunc(
            e,
            ast.CallFunc(
                ast.Name('super'),
                [ast.Name(self.getClassName()), ast.Name("self")],
            ),
        )
        return node

    def visitSwitch(self, e):
        selector = self.dispatch(e.selector)
        cmpvar, init = self.getTmpVar(selector)
        nodes = []
        nodes.append(init)
        body = self.stmt([])
        statevar, init = self.getTmpVar(ast.Const(False))
        nodes.append(init)
        first = True
        default_entry = None
        default_test = None
        for idx, entry in enumerate(e.entries):
            tests = []
            label = entry.test
            stmts = entry.nodes
            if label is not None:
                label = self.dispatch(label)
            code = self.stmt([])
            code.nodes.append(
                ast.Assign(
                    [ast.AssName(statevar, 'OP_ASSIGN')],
                    ast.Const(True),
                ),
            )
            if label is None:
                if idx < len(e.entries) - 1:
                    condition = ast.Compare(
                        ast.Name(statevar),
                        [('is', ast.Const(True))],
                    )
                else:
                    condition = ast.Const(True)
                default_entry = entry
            else:
                condition = ast.Compare(
                    ast.Name(cmpvar), 
                    [('==', label)],
                )
                if not first:
                    condition = ast.Or([
                        ast.Compare(
                            ast.Name(statevar),
                            [('is', ast.Const(True))],
                        ),
                        condition,
                    ])
            if stmts is not None:
                for s in stmts:
                    if isinstance(s, list):
                        code.nodes += self.dispatch_list(s)
                    elif not isinstance(s, jast.TypeNode):
                        node = self.dispatch(s)
                        if isinstance(node, AnonymousClass):
                            raise AstError(s, "AnonymousClass")
                        code.nodes.append(node)
            tests.append((condition, code))
            first = False
            body.nodes.append(ast.If(tests, None))
        if default_entry is not None and default_entry is not entry:
            # We have a default label and it's not the last...
            stmts = default_entry.nodes
            if stmts is not None:
                code = self.stmt([])
                condition = ast.Compare(
                    ast.Name(statevar),
                    [('is', ast.Const(False))],
                )
                for s in stmts:
                    code.nodes.append(self.dispatch(s))
                self.flatten_stmt(code, True)
                tests= [(condition, code)]
                body.nodes.append(ast.If(tests, None))
        body.nodes.append(ast.Break())
        nodes.append(ast.While(
            ast.Const(True),
            body,
            None,
        ))
        self.checkLabel(nodes)
        return self.stmt(nodes)

    def visitThis(self, e):
        node = self.fixCallFunc(
            e,
            ast.Getattr(ast.Name('self'), '__init__'),
        )
        if self.ign_expl_constr:
            return ast.Pass()
        args = self.dispatch_list(e.arguments)
        if self.raise_expl_constr:
            raise AstInitArgs(
                True,
                e.arguments,
                args,
                node,
            )
        return node

    def visitThrow(self, e):
        expr = self.dispatch(e.node)
        node = ast.Raise(expr, None, None)
        return node

    def visitTry(self, e):
        try_block = self.stmt(e.nodes)
        handlers = []
        for catch in e.catches:
            type_ = self.dispatch(catch[0])
            if isinstance(type_, basestring):
                type_ = ast.Name(type_)
            self.addLocal(catch[1].name, None, type_)
            name = self.dispatch(catch[1])
            node = self.stmt(catch[2])
            handlers.append((type_, name, node))
        node = ast.TryExcept(try_block, handlers, None)
        finally_block = None
        if e.finally_ is not None:
            finally_block = self.stmt(e.finally_)
            node = ast.TryFinally(node, finally_block)
        return node

    def visitType(self, e):
        return self.dispatch(e.type)

    def visitTypedSuffix(self, e):
        left = self.dispatch(e.left)
        self.block_self_scope = True
        right = self.dispatch(e.right)
        if isinstance(left, AnonymousClass):
            raise AstError(e, "AnonymousClass")
        node = ast.Getattr(left, right)
        if e.arguments is None:
            # Not method call
            return node
        return self.fixCallFunc(e, node)

    def visitVariableDeclarator(self, e):
        init = e.initializer
        name = e.name
        if init is None:
            a = ast.Pass()
            self.addLocal(name, a)
        else:
            init = self.dispatch(init)
            assert init is not None
            if isinstance(init, AnonymousClass):
                cls, name = self.anonClass(init, ast.Name(name))
                self.addLocal(name, cls)
                return cls
            a = ast.Assign(
                    [ast.AssName(name, 'OP_ASSIGN')],
                    init,
            )
            self.addLocal(name, a.nodes[0], None)
        return a

    def visitWhile(self, e):
        test = self.dispatch(e.test)
        if test is None:
            raise AstError(s, "No condition")
        body = self.stmt(e.nodes)
        node = ast.While(test,body,None)
        node.comment_nodes = [e]
        if e.comments:
            node.comments = [
                [self.parseComment(c) for c in e.comments[0]],
                [],
                [self.parseComment(c) for c in e.comments[1]],
            ]
        nodes = [node]
        self.checkLabel(nodes)
        return self.stmt(nodes)

    def visitUnaryExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.node, None)
        if expr is not None:
            return expr
        raise AstError(e, "visitUnaryExpr operator '%s'" % op)

    # BOTTOM
