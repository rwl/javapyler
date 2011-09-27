#!/usr/bin/env jython

import sys
import os
import time
base = __file__
base = os.path.abspath(base)
base = os.path.dirname(base)
sys.path.append(os.path.abspath(os.path.join(
    base, "javaparser", "antlr-3.3-complete.jar",
)))
sys.path.append(os.path.abspath(os.path.join(
    base, "javaparser", "javaparser.jar",
)))
import ConfigParser
import re
from py2 import ast
import JavaLexer
import JavaParser
from javaparser.JavaWalker import JavaWalker
import javaparser.JavaAst as jast
from org.antlr.runtime import ANTLRFileStream, CommonTokenStream
from fixes import *
from JavaPackages import java_packages


# Monkey patch ast.None to have a default comments
ast.Node.comments = None


file_globals = {}
file_globals_guess = {}
fixme = '!FIXME!'


class Variable(object):

    def __init__(self, name, pyname, pynode=None, jtype=None, 
                 modifiers=None, clashed=False,
                 imp_name=None, imp_path=None,
                ):
        self.name = name
        self.pyname = pyname
        self.pynode = pynode
        self.jtype = jtype
        self.modifiers = modifiers
        self.clashed = clashed
        # Used for global variables based on import
        # java statements
        if imp_name is None:
            self.root_class = None
        else:
            if imp_name.find(os.path.sep) >= 0:
                raise ValueError('imp_name: %s' % imp_name)
            self.root_class = imp_name.split('.')[-1]
        self.imp_name = imp_name
        if imp_path is not None and not imp_path.endswith('.java'):
            raise ValueError('imp_path: %s' % imp_path)
        self.imp_path = imp_path

    def __str__(self):
        return str((self.name, self.pyname, self.pynode, self.jtype, self.modifiers))

    def __repr__(self):
        return repr([self.name, self.pyname, self.pynode, self.jtype, self.modifiers, self.clashed])

    def update(self, name=None, pyname=None,
               pynode=None, jtype=None,
               modifiers=None, clashed=False,
               imp_name=None, imp_path=None,
              ):
            if self.pynode is None:
                self.pynode = pynode
            if self.jtype is None:
                self.jtype = jtype
            if self.modifiers is None:
                self.modifiers = modifiers
            if self.imp_name is None:
                self.imp_name = imp_name
            if self.imp_path is None:
                self.imp_path = imp_path


class Class(object):

    def __init__(self, name, class_stack, depth, methods, locals, node):
        self.name = name
        self.class_stack = class_stack[:]
        self.depth = depth
        self.methods = methods
        self.locals = locals
        self.node = node
        self.move_to_method = []
        self.to_method = None

    def __str__(self):
        return "<Class %s, %d (%d methods, %d locals)>" % (
            self.name, self.depth, len(self.methods), len(self.locals),
        )

    def __repr__(self):
        return self.__str__()

    def initMethod(self, name='__init__'):
        method = self.methods.get(name)
        if method is None:
            method = Method(
                name,
                name,
                self.class_stack + [self],
            )
            self.methods[name] = method
        return method

    def moveToMethod(self, cls, method=None):
        if method is None:
            method = cls.initMethod()
        self.move_to_method.append([cls, method])


class Method(object):

    def __init__(self, name, pyname, class_stack):
        self.name = name
        self.pyname = pyname
        # self.decls is a collection of MethodDeclaration instances
        # the key is the number of arguments (java_args)
        # the content a list of MethodDeclaration instances
        # e.g. if there are two definitions of a method
        # with two arguments/parameters, the dict will be
        # {2: [MDa, MDb]}
        self.decls = {}
        self.base_decl = None
        self.count = 0
        self.node = None
        self.props = None
        self.locals = None
        self.method_var = 'self'
        self.static = None
        self.assigned_globals = {}
        self.class_init = None
        self.class_this = {}
        self.class_stack = class_stack

    def addDeclaration(self, decl):
        #print 'addDeclaration:', self.name, decl.decl_ast.modifiers
        if decl.decl_ast.modifiers is not None and \
           'static' in decl.decl_ast.modifiers:
            assert self.static is not False
            self.method_var = 'cls'
            self.static = True
        else:
            self.static = False
        nargs = len(decl.java_args)
        if not nargs in self.decls:
            self.decls[nargs] = []
        self.decls[nargs].append(decl)
        self.count += 1

    def __str__(self):
        return "<Method %s (%d declarations)>" % (
            self.name, self.count,
        )

    def __repr__(self):
        return self.__str__()


class MethodDeclaration(object):

    def __init__(self, method, node=None, decl_ast=None, java_args=None,
                 java_body=None, java_init=None, defaults=None,
                 py_init=None, py_body=None, is_this=None):
        self.method = method
        self.node = node
        self.decl_ast = decl_ast
        # arguments in java definition:
        self.java_args = [] if java_args is None else java_args
        # java ast of method body:
        self.java_body = java_body
        # None or arguments in this() call
        self.java_init = java_init
        self.defaults = defaults
        self.py_init = py_init
        self.py_body = py_body
        self.is_this = None
        self.args_mismatch = None
        self.this_inits = None
        self.swapped_constants = None
        self.segmented_defaults = None
        self.parent = None
        self.childs = []

        nargs = len(self.java_args)
        if defaults is None:
            self.defaults = [None] * nargs


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


class AnonymousClass(object):

    def __init__(self, cls_node, java_node, this_suffixes):
        self.cls_node = cls_node
        self.java_node = java_node
        self.this_suffixes = this_suffixes
        #assert cls_node.nodes > 0

    def __repr__(self):
        return '<AnonymousClass %r, %s>' % (
            self.java_node, self.java_node.lineno,
        )

class Type(object):

    def __init__(self, name):
        self.name = name


from MapMethod import MapMethod
from MapType import MapType
from MapAttribute import MapAttribute
from MapQualifiedName import MapQualifiedName


class JavaAstToPythonAst(MapAttribute, MapMethod, MapQualifiedName, MapType):
    log_level = 0
    scoped_ignore = [
        'java',
        'super',
        'System',
        # From qualifiedName_map:
        #'sys',
    ]
    reserved_words = [
        'False',
        'None',
        'True'

        'and',
        'as',
        'assert',
        'break',
        'class',
        'continue',
        'def',
        'del',
        'elif',
        'else',
        'except',
        'exec',
        'finally',
        'for',
        'from',
        'global',
        'if',
        'import',
        'in',
        'is',
        'lambda',
        'not',
        'or',
        'pass',
        'print',
        'raise',
        'return',
        'try',
        'while',
        'with',
        'yield',
    ]

    enum_values_name = '_enum_values'
    prefix_private_attrs = '_'
    prefix_private_methods = '' # not used
    prefix_private_vars = '_'
    prefix_clash_attrs = 'v_'

    def __init__(self, srcFile, options=None, **kwargs):
        if options is None:
            self.opts = object()
        else:
            self.opts = options
        for k, v in kwargs.iteritems():
            setattr(self.opts, k, v)
        if not self.opts.as_module:
            self.opts.as_module = False
        self.srcFile = srcFile
        self.absSrcFile = os.path.realpath(os.path.abspath(srcFile))
        self.srcFileBasename = os.path.basename(srcFile)
        path = self.opts.java_base_path
        if path is None:
            path = os.path.dirname(srcFile)
        self.abs_java_base_path = os.path.realpath(os.path.abspath(path))
        self.timing = {}
        t0 = time.time()
        lexer = JavaLexer(ANTLRFileStream(srcFile))
        self.timing['lexer'] = time.time() - t0
        t0 = time.time()
        tokens = CommonTokenStream(lexer)
        self.timing['tokens'] = time.time() - t0
        t0 = time.time()
        parser = JavaParser(tokens)
        self.timing['JavaParser'] = time.time() - t0
        t0 = time.time()
        cu = parser.compilationUnit()
        tree = cu.getTree()
        tree.getToken()
        walker = JavaWalker()
        cu = walker.walk(tree, tokens)
        self.timing['JavaWalker'] = time.time() - t0

        if not hasattr(self, 'java_packages'):
            self.java_packages = java_packages
        self.locals = [{}]
        self.globals = {}
        self.globals_ref = {}
        self.track_global_ref = True
        self.class_to_init = []
        self.class_names = []
        self.class_locals = []
        self.class_stack = []
        self.methods = [{}]
        self.method_var = []
        self.method_stack = []
        self.tmpvars = [{}]
        self.analysing = 0
        self.assigned_globals = None
        self.ign_expl_constr = False
        self.raise_expl_constr = True
        self.this_suffixes = []
        self.state_stack = []
        self.label_stack = []
        self.enum_methods = []
        self.warnings = {}
        self.comments = []
        self.javadocs = []
        self.javalib = {}
        self.unresolvable_imports = {}
        self.deep_imports = {}
        self.type_imports = []
        self.import_assignments = []
        self.block_self_scope = False
        self.add_main = None
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
        self.class_names = []

        t0 = time.time()
        for t in cu.types:
            nodes.append(self.dispatch(t))
        self.timing['typesTranslation'] = time.time() - t0
        if self.timing['typesTranslation'] > 20:
            print srcFile, 'typesTranslation:', self.timing['typesTranslation']

        if self.javalib:
            names = []
            for k, v in self.javalib.iteritems():
                if v > 0:
                    names.append(k)
            if names:
                names.sort()
                javalib = getattr(self.opts, 'javalib')
                if javalib is None:
                    javalib = 'javapyler.java'
                stmt.nodes.append(ast.From(javalib, names, 0, None))

        import_assignments = []
        for target, parent, node in self.import_assignments:
            #if target == 'AnimationHandle':
            #    print 'srcFile:', self.srcFile
            #    print 'import_assignment:', target, parent, target in self.globals_ref
            if target in self.globals_ref:
                #import_assignments.append(node)
                self.globals_ref[parent[0]] = 1
        self.createImports(stmt, cu)
        stmt.nodes += self.type_imports
        import_assignments = []
        for target, parent, node in self.import_assignments:
            if target in self.globals_ref:
                import_assignments.append(node)
        stmt.nodes += import_assignments
        stmt.nodes += nodes
        if self.add_main is not None:
            if self.opts.as_module:
                node = ast.Name('main')
            else:
                node = ast.Getattr(ast.CallFunc(
                    ast.Name(self.add_main), [], None, None,
                ), 'main')
            stmt.nodes += [
                ast.If([(
                    ast.Compare(
                        ast.Name('__name__'),
                        [('==', ast.Const('__main__'))],
                    ),
                    ast.Stmt([
                        ast.Import([('sys', None)]),
                        ast.Discard(ast.CallFunc(
                            node,
                            [ast.Getattr(ast.Name('sys'), 'argv')],
                            None,
                            None,
                        )),
                    ]),
                )], None)]
        self.flatten_stmt(stmt, True)

    def timings(self):
        return self.timing.copy()

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
        if not self.analysing:
            if not name in self.javalib:
                self.javalib[name] = 1
            else:
                self.javalib[name] += 1

    def delJavaLib(self, name):
        if not self.analysing:
            self.javalib[name] -= 1
            assert self.javalib[name] >= 0

    def addGlobal(self, name, pnode=None, jtype=None, modifiers=None, 
                  clashed=None, imp_name=None, imp_path=None):
        pyname = name
        #print 'addGlobal:', self.srcFileBasename, name, imp_name, imp_path
        #if name == 'SelectionModel' and imp_path is not None:
        #    raise ValueError(name)
        if isinstance(modifiers, list) and 'private' in modifiers:
            pyname = '%s%s' % (self.prefix_private_vars, name)
        elif name in self.reserved_words:
            pyname = '%s_' % name
        if not name in self.globals:
            if imp_name is None and imp_path is None:
                # Defined in this file
                imp_path = self.absSrcFile
                add_to_file_globals = True
            else:
                add_to_file_globals = False
            variable = Variable(
                name, pyname, pnode, jtype, modifiers, clashed, 
                imp_name, imp_path,
            )
            self.globals[name] = variable
            if add_to_file_globals and self.getClassDepth() == 0:
                if not self.absSrcFile in file_globals:
                    file_globals[self.absSrcFile] = {}
                file_globals[self.absSrcFile][name] = variable
            if pyname != name:
                self.globals[pyname] = variable
        else:
            self.globals[name].update(
                pynode=pnode,
                jtype=jtype,
                modifiers=modifiers,
                imp_name=imp_name,
                imp_path=imp_path,
            )
        return pyname

    def getGlobal(self, name):
        return self.globals[name]

    def hasGlobal(self, name):
        variable = self.globals.get(name)
        #if name == 'Context':
        #    print 'hasGlobal:', name, variable, variable.root_class
        if variable:
            if self.opts.as_module and self.getClassDepth() == 1:
                pass
            if ((variable.root_class is None or 
                 variable.root_class == name) and 
                self.track_global_ref):
                self.globals_ref[name] = 1
            self.globals_ref[name] = 1
            return True
        return False

    def addLocal(self, name, pnode, jtype, modifiers):
        pyname = self.renameVar(name, pnode, jtype, modifiers)
        if self.opts.as_module:
            if self.getClassDepth() <= 1 and len(self.locals) <= 3:
                return self.addGlobal(name, pnode, jtype, modifiers)
        clashed = False
        if self.localsIsClassLocals() and \
           pyname in self.methods[-1]:
            pyname = self.clashRenameVar(name, pnode, jtype, modifiers)
            if name != pyname:
                renamed = " (variable renamed to '%s')" % pyname
            else:
                renamed = ''
            self.warning(
                jtype.lineno,
                "Variable and method variable name clash '%s'%s" % (
                    name, renamed
                ),
            )
            if name in self.locals[-1]:
                variable = self.locals[-1][name]
                variable.pyname = pyname
                variable.clashed = True
                if not pyname in self.locals[-1]:
                    self.locals[-1][pyname] = self.locals[-1][name]
                else:
                    assert self.locals[-1][pyname] is variable
        if not name in self.locals[-1]:
            variable = Variable(name, pyname, pnode, jtype, modifiers, clashed)
            self.locals[-1][name] = variable
            if pyname != name:
                assert not pyname in self.locals[-1]
                self.locals[-1][pyname] = self.locals[-1][name]
        else:
            self.locals[-1][name].update(
                pynode=pnode,
                jtype=jtype,
                modifiers=modifiers,
            )
        return pyname

    def getLocal(self, name):
        if self.opts.as_module:
            if name in self.locals[-1]:
                return self.locals[-1][name]
            return self.globals[name]
        return self.locals[-1][name]

    def hasLocal(self, name):
        return name in self.locals[-1]

    def getClassLocal(self, name, depth=-1):
        depth = self.class_locals[depth]
        return self.locals[depth].get(name, None)

    def hasClassLocal(self, name, depth=-1):
        depth = self.class_locals[depth]
        return name in self.locals[depth]

    def localsIsClassLocals(self):
        return len(self.locals) == self.class_locals[-1] + 1

    def renameVar(self, name, pnode, jtype, modifiers):
        if isinstance(modifiers, list):
            if ('private' in modifiers) or \
               (not modifiers and self.localsIsClassLocals()):
                return '%s%s' % (self.prefix_private_attrs, name)
        if name in self.reserved_words:
            return '%s_' % name
        return name

    def clashRenameVar(self, name, pnode, jtype, modifiers):
        name = '%s%s' % (self.prefix_clash_attrs, name)
        return self.renameVar(name, pnode, jtype, modifiers)

    def guessNodeType(self, node):
        def javaType(jtype):
            if isinstance(jtype, jast.Type):
                if jtype.array:
                    jtypes = ['Vector']
                else:
                    jtypes = javaType(jtype.type)
            elif isinstance(jtype, jast.Class):
                if isinstance(node, ast.Name):
                    jtypes = [node.name]
                else:
                    jtypes = None
                    if isinstance(node, ast.Getattr):
                        attrname = node.attrname
                        if isinstance(attrname, basestring):
                            jtypes = [attrname]
                        elif isinstance(attrname, ast.Name):
                            jtypes = [attrname.name]
                    if jtypes is None:
                        raise ValueError("Cannot determine java type from %r" % (
                            node,
                        ))
                if jtype.extends:
                    for n in jtype.extends:
                        jtypes += javaType(n)
                if jtype.implements:
                    for n in jtype.implements:
                        jtypes += javaType(n)
            elif isinstance(jtype, jast.PrimitiveType):
                jtypes = [jtype.name]
            elif isinstance(jtype, jast.ClassOrInterfaceType):
                jtypes = [t[0] for t in jtype.types]
            else:
                raise ValueError("Not implemetend: %s" % jtype)
            return jtypes
            
        if isinstance(node, ast.Name):
            name = node.name
        elif isinstance(node, ast.Getattr):
            name = node.attrname
        else:
            return []
        scopes = [self.globals] + self.locals
        scopes.reverse()
        for scope in scopes:
            if name in scope:
                jtype = scope[name].jtype
                if jtype is None:
                    continue
                return javaType(jtype)
        return [name]

    def pushClassName(self, name, node):
        #if self.class_names and self.class_names[-1] == name:
        #    raise ValueError(name)
        self.descend()
        self.methods.append({})
        self.class_locals.append(len(self.locals) - 1)
        self.class_names.append(name)
        cls = Class(
            name,
            self.class_stack,
            len(self.class_stack) + 1,
            self.methods[-1],
            self.locals[-1],
            node,
        )
        self.class_stack.append(cls)

    def popClassName(self):
        self.ascend()
        self.methods.pop()
        self.class_locals.pop()
        name = self.class_names.pop()
        cls = self.class_stack.pop()
        return name

    def getClassName(self, level=-1):
        return self.class_names[level]

    def getClassDepth(self):
        return len(self.class_stack)

    def addMethod(self, name):
        if name is None:
            raise ValueError('addMethod name is None')
        methods = self.methods[-1]
        if not name in methods:
            pyname = self.renameVar(name, None, None, None)
            methods[name] = Method(name, pyname, self.class_stack)
        return methods[name]

    def getMethod(self, name):#, java_args=False):
        return self.methods[-1].get(name, None)
        method = self.methods[-1].get(name, None)
        if java_args is False or method is None:
            return method
        if java_args is None or len(java_args) == 0:
            if len(method.decls[0]) == 1:
                return method.decls[0][0]
            return method.decls[0]
        nargs = len(java_args)
        for m in method.decls[nargs]:
            if m.java_args == java_args:
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
        line = "%s(%s): %s" % (self.srcFile, lineno, line)
        if not line in self.warnings:
            self.warnings[line] = True
            sys.stderr.write("%s\n" % line)

    def parseComment(self, comment):
        if comment is None:
            return None
        if isinstance(comment, list):
            if not comment:
                return None
            comment = "\n".join(comment)
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
        t0 = time.time()

        def unresolvable(impname):
            name = impname.split('.')[-1]
            name = self.addGlobal(name, imp_name=impname)
            if not impname in file_globals_guess:
                file_globals_guess[impname] = {}
            file_globals_guess[impname][name] = None
            if not impname in self.unresolvable_imports:
                self.unresolvable_imports[impname] = []
            if not name in self.unresolvable_imports[impname]:
                self.unresolvable_imports[impname].append(name)

        def handleFile(fpath, impname):
            if fpath.endswith('package-info.java'):
                return
            if fpath == self.absSrcFile:
                return
            if os.path.isdir(fpath):
                for f in os.listdir(fpath):
                    if f.endswith('.java'):
                        p = os.path.join(fpath, f)
                        handleFile(
                            os.path.join(fpath, f),
                            "%s.%s" % (impname, f[:-5]),
                        )
                return
            assert fpath[-5:] == '.java'
            splitted_fpath = fpath[:-5].split(os.path.sep)
            splitted_impname = impname.split('.')
            assign = []
            while splitted_fpath:
                _fpath = "%s.java" % os.path.sep.join(splitted_fpath)
                if _fpath in file_globals:
                    for name in file_globals[_fpath]:
                        variable = file_globals[_fpath][name]
                        name = self.addGlobal(
                            name,
                            jtype=variable.jtype,
                            imp_name=variable.imp_name, 
                            imp_path=variable.imp_path,
                        )
                    if assign:
                        self.addDeepImport(
                            splitted_impname,
                            splitted_fpath[-1:] + assign,
                            assign[-1],
                            _fpath,
                        )
                    return
                if _fpath in file_globals_guess:
                    if not file_globals_guess[_fpath]:
                        unresolvable(impname)
                    else:
                        for name in file_globals_guess[_fpath]:
                            self.addGlobal(name, imp_name=impname, imp_path=_fpath)
                        if assign:
                            self.addDeepImport(
                                splitted_impname,
                                splitted_fpath[-1:] + assign,
                                assign[-1],
                                _fpath,
                            )
                    return
                assign.insert(0, splitted_fpath.pop())
            if impname is not None and impname in file_globals_guess:
                # e.g. impname: junit.framework.TestCase {u'TestCase': None}
                #      file_globals_guess[impname]: {u'TestCase': None}
                for name in file_globals_guess[impname]:
                    self.addGlobal(name, imp_name=impname)
                return
            if os.path.isfile(fpath):
                file_globals_guess[fpath] = {}
                fp = open(fpath, 'r')
                data = fp.read()
                fp.close()
                splitted_impname = impname.split('.')
                #for regex in [re_class, re_enum, re_interface]:
                for regex in [re_any]:
                    for r in regex.findall(data):
                        name = r[-1]
                        indent = r[0].replace('\n', '')
                        if name:
                            if indent:
                                pyname = self.addDeepImport(
                                    splitted_impname,
                                    [splitted_impname[-1], name],
                                    name,
                                    fpath,
                                )
                            else:
                                pyname = self.addGlobal(name, imp_name=impname, imp_path=fpath)
                            file_globals_guess[fpath][pyname] = None
            elif os.path.isdir(fpath):
                pass
                raise NotImplementedError()
            elif impname is not None:
                splitted_fpath = fpath.split(os.path.sep)
                splitted_impname = impname.split('.')
                name = []
                target = splitted_impname[-1]
                while splitted_impname:
                    splitted_fpath.pop()
                    fpath = "%s%s.java" % (os.path.sep, os.path.join(*splitted_fpath))
                    name.insert(0, splitted_impname.pop())
                    if fpath in file_globals:
                        pass
                    elif os.path.isfile(fpath):
                        handleFile(fpath, '.'.join(splitted_impname))
                    else:
                        continue
                    name.insert(0, splitted_impname.pop())
                    self.addDeepImport(splitted_impname, name, target, fpath)
                    break
                if not splitted_impname:
                    unresolvable(impname)
            else:
                unresolvable(impname)
        src = self.absSrcFile.split(os.path.sep)
        dirname = os.path.dirname(self.absSrcFile)
        base = dirname[len(self.abs_java_base_path):].split(os.path.sep)
        base = '.'.join(base)
        if self.opts.java_base is not None:
            base = "%s%s" % (self.opts.java_base, base)
        templ = '(\s*)(((abstract)|(public)).*)?\s+%s\s+(\w+).*'
        templ = '^(\s*).*?\s+%s\s+(\w+).*?{'
        templ = '^(\s*)(((public)|(abstract)|(static)|(final))\s+)*%s\s+(\w+).*?{'
        re_class = re.compile(templ % 'class', re.M|re.S)
        re_enum = re.compile(templ % 'enum', re.M|re.S)
        re_interface = re.compile(templ % 'interface', re.M|re.S)
        templ = '^(\s*)(((public)|(abstract)|(static)|(final))\s+)*((class)|(interface)|(enum))\s+(\w+).*?{'
        re_any = re.compile(templ, re.M|re.S)
        for f in os.listdir(dirname):
            if f.endswith('.java'):
                fpath = os.path.join(dirname, f)
                impname = "%s.%s" % (base, f[:-5])
                handleFile(fpath, impname)
        imports = []
        if self.package_name:
            imports.append(self.package_name)
        if cu.imports:
            imports += [i for i in cu.imports]
        for imp in imports:
            if isinstance(imp, basestring):
                impname = imp
                wildcard = True
            else:
                impname = imp.name
                wildcard = imp.wildcard
            if wildcard and impname in self.java_packages:
                for name in self.java_packages[impname]:
                    self.addGlobal(name, imp_name=impname)
                continue
            imp = impname.split('.')
            while not imp[0] in src:
                imp.pop(0)
                if not imp:
                    break
            if not imp:
                unresolvable(impname)
                continue
            fpath = src[:src.index(imp[0])] + imp
            if wildcard:
                fpath = os.path.sep.join(fpath)
            else:
                fpath = "%s.java" % os.path.sep.join(fpath)
            handleFile(fpath, impname)
        self.timing['collectGlobals'] = time.time() - t0
        return

    def addDeepImport(self, impname, name, target, fpath=None):
        #if target == 'Coordinates':
        #    print 'srcFile:', self.srcFile
        #    print 'addDeepImport:', impname, name, target
        if isinstance(impname, basestring):
            impname = impname.split('.')
        if isinstance(name, basestring):
            name = name.split('.')
        if target in self.deep_imports:
            return self.deep_imports[target][3]
        pyname = self.addGlobal(target, imp_name='.'.join(impname), imp_path=fpath)
        self.deep_imports[target] = (impname, name[:], target, pyname)
        src = ast.Name(name[-1])
        name.pop()
        while name:
            src = ast.Getattr(
                ast.Name(name.pop()),
                src,
            )
        node = ast.Assign(
            [ast.AssName(pyname, 'OP_ASSIGN')],
            src,
        )
        self.import_assignments.append((pyname, self.deep_imports[target][1], node))
        return pyname

    def mapImport(self, name):
        base = self.opts.java_base
        if base:
            if name.startswith(base + '.'):
                name = name[len(base) + 1:]
                if name[0] == '.':
                    name = name[1:]
                if self.opts.py_base:
                    name = "%s.%s" % (self.opts.py_base, name)
        return name

    def addTypeImport(self, name):
        name = self.mapImport(name)
        self.type_imports.append(
            ast.Import([name]),
        )
        return name

    def addImportFrom(self, node, name, names, as_module):
        if as_module:
            names = name.split('.')
            name = '.'.join(names[:-1])
            names = [names[-1]]
        name = self.mapImport(name)
        for n in node.nodes:
            if isinstance(n, ast.From) and n.modname == name:
                for name in names:
                    if not name in n.names:
                        n.names.append(name)
                n.names.sort()
                return
        node.nodes.append(ast.From(name, names, 0, None))

    def createImports(self, node, cu):
        unresolved = self.globals_ref.keys()
        unresolved.sort()
        files = {}
        for u in unresolved:
            if not u in self.globals:
                print self.globals.keys()
            fpath = self.globals[u].imp_path
            impname = self.globals[u].imp_name
            if fpath is None:
                found = False
                for fpath in file_globals:
                    if u in file_globals[fpath]:
                        found = True
                        file_globals[fpath][u].imp_path = fpath
                        break
                # Also not found for as_module globals
                if not found and not self.opts.as_module:
                   self.warning(0, 'Could not resolve import for %s (%s)' % (u, impname))
                   continue
            if fpath == self.absSrcFile:
                continue
            if not fpath in files:
                files[fpath] = {}
            name = os.path.split(fpath)[-1]
            name = os.path.splitext(name)[0]
            if name != u and fpath.endswith('.java'):
                impname = os.path.splitext(fpath)[0].split(os.path.sep)
                target = self.globals[u].pyname
                self.addDeepImport(impname, [name, target], target)
            files[fpath][name] = 1
        if self.package_name is None:
            pkg = []
        else:
            pkg = self.package_name.split('.')
        for fpath in files:
            if fpath == self.absSrcFile:
                continue
            as_module = False
            for section in self.opts.config.sections():
                if fpath.find(section) >= 0:
                    try:
                        as_module = self.opts.config.getboolean(
                            section, 'as_module',
                        )
                    except ConfigParser.NoOptionError, ValueError:
                        pass
                    break
            names = files[fpath].keys()
            names.sort()
            name = fpath.split(os.path.sep)
            if name[-len(pkg)-1:-1] == pkg:
                name = '.'.join(name[-len(pkg)-1:])
                name = name[:-5]
                self.addImportFrom(node, name, names, as_module)
            else:
                name[-1] = name[-1][:-5]
                parent_name = '.'.join(name[:-1])
                impname = '.'.join(name)
                for imp in cu.imports:
                    if impname.endswith(imp.name):
                        self.addImportFrom(node, imp.name, names, as_module)
                    elif imp.wildcard and parent_name.endswith(imp.name):
                        impname = '%s.%s' % (imp.name, name[-1])
                        self.addImportFrom(node, impname, names, as_module)
        comments = []
        importnames = self.unresolvable_imports.keys()
        importnames.sort()
        for name in importnames:
            names = self.unresolvable_imports[name]
            names.sort()
            if names == ['*']:
                name = '.'.join(name.split('.')[:-1])
                names = '*'
            else:
                names = "(%s,)" % ', '.join([n for n in names])
            comments.append("from %s import %s" % (
                name,
                names,
            ))
        if comments:
            empty = ast.EmptyNode()
            empty.comments = comments
            node.nodes.append(empty)

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
                if op == '++':
                    args = [left_expr]
                    fname = 'PREINC'
                    self.addJavaLib(fname)
                    return self.libCallGlobalsAndLocals(fname, args)
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
            if op in ['==', '!=']:
                compare = op
                compare_override = 'is' if op == '==' else 'is not'
                for side in left, right:
                    if isinstance(side, jast.Literal):
                        if side.type == 'null':
                            compare = compare_override
                            break
                    elif isinstance(side, jast.Identifier):
                        if side.name == 'this':
                            compare = compare_override
                            break
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
        def backmap(node):
            if isinstance(node, ast.CallFunc):
                if isinstance(node.node, ast.Name):
                    name = node.node.name
                    if name in ['POSTINC', 'POSTDEC', 'PREINC', 'PREDEC']:
                        if len(node.orig_args) == 1:
                            if name[-3:] == 'INC':
                                op = '+='
                            else:
                                op = '-='
                            left_expr = node.orig_args[0]
                            node = ast.AugAssign(left_expr, op, ast.Const(1))
                            self.delJavaLib(name)
                            node.backmap = name
            elif isinstance(node, ast.Return):
                value = backmap(node.value)
                if value is not node.value:
                    nodes = []
                    if value.backmap.startswith('POST'):
                        retvar, init = self.getTmpVar(value.node)
                        nodes.append(init)
                        nodes.append(value)
                        nodes.append(ast.Return(ast.Name(retvar)))
                    node = ast.Stmt(nodes)
            return node

        def addEmpty(nodes, node):
            if node.comments:
                if (
                    len(nodes) > 0 
                    and (
                        isinstance(nodes[-1], ast.EmptyNode)
                        or isinstance(nodes[-1], ast.Pass)
                    )
                ):
                    empty = nodes[-1]
                    if not empty.comments:
                        empty.comments = node.comments
                    else:
                        empty.comments += node.comments
                else:
                    empty = ast.EmptyNode()
                    empty.comments = node.comments
                    nodes.append(empty)
                node.comments = None

        def flatten(node):
            nodes = []
            comment_node = node
            for n in node.nodes:
                if isinstance(n, Type):
                    raise TypeError(n)
                if isinstance(n, ast.Stmt):
                    n = flatten(n)
                    nodes += n.nodes
                    addEmpty(nodes, n)
                elif isinstance(n, ast.EmptyNode):
                    addEmpty(nodes, n)
                elif isinstance(n, ast.Pass):
                    addEmpty(nodes, n)
                else:
                    if isinstance(n, AnonymousClass):
                        print 'AnonymousClass in flatten_stmt:', repr(n)
                    n = backmap(n)
                    if isinstance(n, ast.Stmt):
                        n = flatten(n)
                        addEmpty(nodes, n)
                        nodes += n.nodes
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
                raise ValueError("list expected, got %r" % nodes)
            for n in nodes:
                if isinstance(n, ast.Node):
                    node.nodes.append(n)
                elif isinstance(n, list):
                    node.nodes += self.dispatch_list(n)
                    self.flatten_stmt(node)
                elif not isinstance(n, jast.TypeNode):
                    n = self.dispatch(n)
                    if isinstance(n, AnonymousClass):
                        print 'AnonymousClass in stmt:', repr(AnonymousClass)
                    node.nodes.append(n)
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

    def dispatch(self, v, **kwargs):
        method = v.__class__.__name__.split('.')[-1]
        method = 'visit%s' % method
        node = getattr(self, method)(v, **kwargs)
        if not self.analysing:
            if isinstance(node, AnonymousClass):
                self.anon_count = getattr(self, 'anon_count', 0)
                if self.anon_count >= 1000:
                    raise AstError(v, "AnonymousClass")
                self.anon_count += 1
                return self.node(v, node)
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
            if isinstance(v, list):
                # Probably nested array creator
                v = self.dispatch_list(v)
            else:
                v = self.dispatch(v)
            if isinstance(v, Type):
                continue
            #if isinstance(v, basestring):
            #    v = ast.Name(v)
            if isinstance(v, list):
                dst += v
            else:
                dst.append(v)
        return dst
     
    def scoped(self, name, test_globals=False, callable=False, as_string=False):
        debug = 0
        debug_on = []
        #debug_on = ['getNextActivity']
        for d in debug_on:
            if name.find(d) >= 0:
                debug = 1
                break
        #if debug: raise ValueError(name)
        if name == 'sys.stdin' and not self.block_self_scope:
            raise ValueError('stdin')
        def pynode(names):
            if as_string:
                return '.'.join(names)
            node = names.pop(0)
            if isinstance(node, basestring):
                node = ast.Name(node)
            if len(names) > 0:
                attrname = names[-1]
                node = ast.Getattr(node, names.pop(0))
                while len(names) > 0:
                    node = ast.Getattr(node, names.pop(0))
                if attrname in self.attrib_map:
                    node = self.mapAttrib(node, attrname)
            return node
        names = name.split('.')
        for i, n in enumerate(names):
            if n in self.reserved_words:
                names[i] = "%s_" % n
        base_name = names[0]
        if base_name in self.type_map:
            names[0] = self.mapType(name)
            self.block_self_scope = False
            return pynode(names)
        if (
            base_name in self.method_var or
            base_name in self.scoped_ignore
        ):
            self.block_self_scope = False
            return pynode(names)
        if self.hasLocal(base_name):
            self.block_self_scope = False
            names[0] = self.getLocal(base_name).pyname
            return pynode(names)
        if callable and len(names) == 1:
            method = self.class_stack[-1].methods.get(base_name)
            if method is not None:
                self.block_self_scope = False
                names[0] = method.pyname
                if not self.localsIsClassLocals():
                    names = [self.method_stack[-1].method_var] + names
                return pynode(names)
        if self.block_self_scope:
            self.block_self_scope = False
            return pynode(names)
        depth = self.getClassDepth()
        max_depth = depth - 1
        if debug: print base_name, 'max_depth:', max_depth, 'callable:', callable
        while depth:
            depth -= 1
            cls = self.class_stack[depth]
            if debug: print base_name, 'cls:', cls
            store = None
            if callable and base_name in cls.methods:
                store = cls.methods
            elif base_name in cls.locals:
                store = cls.locals
            if store is not None:
                if depth != max_depth:
                    self.class_stack[depth + 1].to_method = True
                localvar = store[base_name]
                names[0] = localvar.pyname
                method = None
                for method in reversed(self.method_stack):
                    if debug: print 'base_name:', base_name, cls, method.class_stack, self.class_stack, self.method_stack
                    if method.class_stack[-1] is cls:
                        if debug: print base_name, 'is cls:', cls
                        break
                    method = None
                if debug: print 'method:', method
                if method is None:
                    name_base = cls.name + '_this'
                    if len(self.class_stack) < depth + 2:
                        if debug: print name_base, 'len(self.class_stack), depth:', len(self.class_stack), depth
                        pass
                    else:
                        self.class_stack[depth + 1].moveToMethod(
                        cls, cls.initMethod(),
                    )
                    names = [name_base] + names
                else:
                    if debug: print base_name, 'max_depth, depth:', max_depth, depth
                    if depth == max_depth:
                        names = [method.method_var] + names
                    elif False and (localvar.modifiers is not None and
                        'static' in localvar.modifiers):
                        if debug: print base_name, 'static'
                        names = [c.name 
                                 for c in self.class_stack[:depth + 1]
                                ] + names
                    else:
                        name_base = cls.name + '_this'
                        names = [name_base] + names
                        method.class_this[name_base] = [
                            method.method_var,
                            name_base + "BAA",
                        ]
                return pynode(names)

        if (True or test_globals) and self.hasGlobal(base_name):
            if debug: print base_name, 'hasGlobal'
            self.block_self_scope = False
            names[0] = self.getGlobal(base_name).pyname
            return pynode(names)
        if True or max_depth <= 1:
            if debug: print base_name, 'method_stack:', self.method_stack
            if self.method_stack:
                method = self.method_stack[-1]
                names = [method.method_var] + names
            elif len(names) > 1:
                pass
            else:
                base = 'SELF'
                if len(self.class_stack) > 0:
                    cls = self.class_stack[-1]
                    if len(cls.node.bases) == 1:
                        base = cls.node.bases[0]
                names = [base] + names
        return pynode(names)

    def node(self, java_node, py_node):
        if java_node.comments:
            # Track the nodes done, in case we vitit this multiple times
            if not hasattr(py_node, 'comments_done'):
                py_node.comments_done = []
            if java_node not in py_node.comments_done:
                py_node.comments_done.append(java_node)
                comments = java_node.comments
                comments = [self.parseComment(c) for c in comments]
                if py_node.comments is None:
                    py_node.comments = comments
                else:
                    py_node.comments += comments
        py_node.lineno = java_node.lineno
        return py_node

    def libCallGlobalsAndLocals(self, name, arguments):
        # The globals/locals are only added when the argument
        # is convertable to a string (constant)
        # This will make sure that you'll get an error on
        # invocations of ((POST)|(PRE))|((INC)|(DEC)) when
        # the argument is not a single string
        args = arguments[:]
        if len(args) == 1:
            a = args[0]
            if isinstance(a, ast.Name):
                args = [ast.Const(a.name)]
            elif isinstance(a, ast.Getattr):
                names = []
                while isinstance(a, ast.Getattr):
                    names.append(a.attrname)
                    a = a.expr
                if isinstance(a, ast.Name):
                    names.append(a.name)
                    names.reverse()
                    args = [ast.Const('.'.join(names))]
            if isinstance(args[0], ast.Const):
                args = [
                    ast.CallFunc(ast.Name('globals'), []),
                    ast.CallFunc(ast.Name('locals'), []),
                ] + args
        node = ast.CallFunc(ast.Name(name), args)
        node.orig_args = arguments
        return node

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
        #if method.count <= 1:
        #    return method
        method_nargs = method.decls.keys()
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
            dst = base.defaults
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
            if child.parent:
                return child.parent
            if child.is_this is None:
                child.parent = None
                return None
            nargs = len(child.java_init)
            candidates = method.decls[nargs]
            parent = None
            if len(candidates) == 1:
                parent = candidates[0]
            else:
                parent = candidates
            child.parent = parent
            for p in candidates:
                p.childs.append(child)
            return parent

        method_nargs = method.decls.keys()
        method_nargs.sort()
        minargs = method_nargs[0]
        maxargs = method_nargs[-1]
        method_nargs.reverse()
        for nargs in method_nargs:
            this_inits[nargs] = []
            methlist = method.decls[nargs]
            for m in methlist:
                java_args = m.java_args
                java_init = m.java_init
                py_init = m.py_init
                is_this = m.is_this
                parent = findParent(m)
                if isinstance(parent, list):
                    generic_args = True
                    #print 'generic_args 2'
                    multi_parents = True
                if base is None:
                    base = m
                else:
                    if not argsMatch(java_args,  base.java_args, nargs):
                        generic_args = True
                        #print 'generic_args 3'
                        args_mismatch.append(m)
                        m.args_mismatch = True
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
                        m.this_inits = [i for i in segments[0]]
                        this_inits[nargs].append(m.this_inits)
                elif len(segments) == 2:
                    if not isinstance(segments[0][0], ast.Const):
                        # Second half is constants
                        setDefaults(len(segments[0]), segments[1])
                    else:
                        # First half is constants
                        generic_args = True
                        #print 'generic_args 4'
                        swapped_constants.append(m)
                        m.swapped_constants = segments
                else:
                    # Segmented defaults
                    generic_args = True
                    #print 'generic_args 5'
                    segmented_defaults.append(m)
                    m.segmented_defaults = segments

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
        method.props = props
        method.locals = self.locals[-1]
        return method

    def getNormalPyMethod(self, name, method, base, props):

        def getBodyNodes(m):
            java_body = m.java_body
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
            childs = [(len(m.java_args), m) for m in childs]
            childs.sort()
            return [m for n, m in childs]

        def getChildInits(methods):
            if len(methods) == 0:
                return []
            have_init = False
            ifnode = ast.If([], None)
            for m in methods:
                if not m.this_inits:
                    continue
                have_init = True
                init_nodes = []
                idx = -1
                for src in m.this_inits:
                    idx += 1
                    if isinstance(src, ast.Name):
                        continue
                    dst = base.java_args[idx]
                    dst = dst.name
                    if isinstance(src, AnonymousClass):
                        raise AstError(e, "AnonymousClass")
                    init_nodes.append(
                        ast.Assign(
                            [ast.AssName(dst, 'OP_ASSIGN')],
                            src,
                        ),
                    )
                nargs = len(m.java_args)
                if nargs >= len(base.java_args):
                    ifnode.else_ = self.stmt(init_nodes)
                else:
                    arg = base.java_args[nargs]
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
                parent = m.parent
                childs = sortedChilds(m.childs)
                py_body = getChildInits(childs)
                nodes = [ast.Pass()] + py_body
                py_body.append(getBodyNodes(m))
                if parent is None:
                    nodes = py_body + getNodes(childs)
                else:
                    ifnode = ast.If([], None)
                    nargs = len(m.java_args)
                    parent_nargs = len(parent.java_args)
                    cmpop = 'is'
                    if parent_nargs > len(base.java_args):
                        cmpop = 'is not'
                        arg = base.java_args[parent_nargs - 1]
                        default = base.defaults[parent_nargs - 1]
                    elif parent_nargs == 0:
                        arg = base.java_args[nargs - 1]
                        default = base.defaults[nargs - 1]
                    else:
                        arg = base.java_args[nargs - 1]
                        default = base.defaults[nargs - 1]
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
                    py_body += getNodes(sortedChilds(m.childs))
                    self.flatten_stmt(stmt, True),
                    nodes = [ifnode]
            else:
                ifnode = ast.If([], None)
                comment_node = ifnode
                for m in methods:
                    childs = sortedChilds(m.childs)
                    py_body = getChildInits(childs)
                    nodes = [comment_node] + py_body
                    py_body.append(getBodyNodes(m))
                    nargs = len(m.java_args)
                    stmt = self.stmt(py_body)
                    if nargs >= len(base.java_args):
                        ifnode.else_ = stmt
                    else:
                        arg = base.java_args[nargs]
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
            return self.stmt(nodes).nodes

        method_nargs = method.decls.keys()
        method_nargs.sort()
        minargs = method_nargs[0]
        maxargs = method_nargs[-1]

        defaults = [ast.Const(None) for i in base.defaults if i is not None]
        #while defaults and defaults[0] is None:
        #    defaults.pop(0)
        # Transform bodies to python
        # From least to most args
        # Parent to childs
        nothis_methods = props['nothis_methods']
        nodes = getNodes(nothis_methods)

        n = len(base.java_args) - len(defaults)
        while n > minargs:
            n -= 1
            defaults.insert(0, ast.Const(None))
        node = self.stmt(nodes, True)
        base.py_body = node
        base.defaults = defaults
        method.base_decl = base

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
                for b in java_body:
                    if isinstance(b, list):
                        self.dispatch_list(b)
                    else:
                        self.dispatch(b)
                init_args = None
            except AstInitArgs, a:
                pass
            for p in parameters:
                p.name = self.addLocal(p.name, p, None, None)
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
            return self.stmt(py_body).nodes

        def getNodes(m):
            params = m.java_args
            if params:
                parameters = self.dispatch_list(params)
            else:
                parameters = []
            if not self.opts.as_module or self.getClassDepth() > 1:
                parameters.insert(0, ast.Name(self.method_var[-1]))

            nodes = []
            args = [ast.AssName(a.name, 'OP_ASSIGN') for a in parameters[1:]]
            if len(args) > 0:
                nodes.append(
                    ast.Assign([ast.AssTuple(args)], ast.Name(argsvar)),
                )

            # Get python ast for java_body
            nodes += getBodyNodes(parameters, m.java_body)
            return self.stmt(nodes).nodes

        def argsDiff(nargs, methlist):
            save_track_global_ref = self.track_global_ref
            self.track_global_ref = False
            methlist = methlist[:]
            methtypes = []
            for m in methlist:
                methtypes.append([])
                for a in m.java_args:
                    a = self.dispatch(a.type)
                    if hasattr(a, 'name'):
                        a = a.name
                    t = self.mapType(a)
                    methtypes[-1].append(t)
                methtypes[-1].append(m)
            methtypes.sort()
            self.track_global_ref = save_track_global_ref
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
            nodes = argsDiffNodes(nargs, diffs[types[-1]])
            ifnode = ast.If([], self.stmt(nodes))
            for t in types[:-1]:
                self.scoped(t, test_globals=True)
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

        method_nargs = method.decls.keys()
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

            methlist = method.decls[nargs]
            if len(methlist) == 1:
                m = methlist[0]
                test[1].nodes += getNodes(m)
                continue
            test[1].nodes += argsDiffNodes(nargs, methlist)
        node = self.stmt(nodes, True)
        base.defaults = []
        base.py_body = node
        method.base_decl = base

    def methodAppendDoc(self, name, doc):
        if doc is not None:
            node = self.getMethod(name).node
            node.doc = "%s\n---\n%s" % (
                node.doc,
                self.parseComment(doc),
            )
        return ast.Pass()

    def methodDeclaration(self, d, name, doc, params, body):
        if 'static' in d.modifiers:
            self.method_var.append('cls')
        else:
            self.method_var.append('self')
        if not self.analysing:
            classLocal = self.getClassLocal(name)
            if classLocal is not None and \
               classLocal.pyname == name:
                self.warning(
                    d.lineno,
                    "Method and variable name clash '%s'" % name,
                )
        else:
            ign_expl_constr = self.ign_expl_constr
            self.ign_expl_constr = False
            if self.opts.as_module and self.getClassDepth() <= 1:
                self.addGlobal(name)
            #name = self.addLocal(name, None, d.typeParameters, d.modifiers)
            d.name = name
            self.descend()
            for p in params:
                p.name = self.addLocal(p.name, None, p.type, None)
            method = self.addMethod(name)
            self.method_stack.append(method)
            try:
                depth = self.getStackDepth()
                if body is not None:
                    for b in body:
                        if isinstance(b, list):
                            self.dispatch_list(b)
                        else:
                            self.dispatch(b)
                m = MethodDeclaration(
                    method,
                    decl_ast=d,
                    java_args=params,
                    java_body=body,
                )
                method.addDeclaration(m)
                #self.addMethod(
                #    name, 
                #    decl_ast=d,
                #    java_args=params,
                #    java_body=body,
                #)
            except AstInitArgs, a:
                self.setStackDepth(depth)
                m = MethodDeclaration(
                    method,
                    decl_ast=d,
                    java_args=params,
                    java_init=a.java_init,
                    py_init=a.py_init,
                    java_body=body,
                #    excpl_constr=a.node,
                    is_this=a.is_this,
                )
                method.addDeclaration(m)
                #self.addMethod(
                #    name,
                #    decl_ast=d,
                #    java_args=params,
                #    java_init=a.java_init,
                #    py_init=a.py_init,
                #    java_body=body,
                #    excpl_constr=a.node,
                #    is_this=a.is_this,
                #)
            except:
                print 'method exception'
                raise
            self.ascend()
            self.method_stack.pop()
            self.method_var.pop()
            self.ign_expl_constr = ign_expl_constr
            return ast.Pass()
        decorators = None
        defaults = None
        method = self.getMethod(name)
        if method is None:
            raise ValueError('Method is None: %s' % name)
        if method.props:
            self.methodAppendDoc(name, doc)
            self.method_var.pop()
            return ast.Pass()
        self.method_stack.append(method)
        save_assigned_globals = self.assigned_globals
        self.assigned_globals = method.assigned_globals
        self.descend()
        for p in params:
            p.name = self.addLocal(p.name, None, p.type, None)
        method = self.analyseMethodDefs(name)
        self.ascend()
        generic_args = method.props['generic_args']
        base = method.base_decl
        params = base.java_args
        defaults = base.defaults
        code = base.py_body
        pyname = method.pyname
        if params:
            parameters = self.dispatch_list(params)
        else:
            parameters = []
        if name == 'main' and self.getClassDepth() <= 1:
            self.add_main = self.class_names[-1]
        if not self.opts.as_module or self.getClassDepth() > 1:
            parameters.insert(0, ast.Name(self.method_var[-1]))
            if 'static' in d.modifiers:
                decorators = ast.Decorators([ast.Name('classmethod')])
        flags = 0
        if generic_args:
            flags = ast.CO_VARARGS
            parameters = parameters[:1]
            parameters.append(ast.Name('args'))
        elif len(params) > 0 and '...' in params[-1].type_modifier:
            flags = ast.CO_VARARGS
        if method.class_init:
            assert isinstance(code, ast.Stmt)
            if isinstance(method.class_init[0], ast.Stmt) and \
               len(method.class_init[0].nodes) == 0:
                del(method.class_init[0])
            if method.class_init:
                code.nodes = method.class_init + code.nodes
        if method.class_this:
            for src, dst in method.class_this.values():
                code.nodes.insert(0, ast.Assign(
                    [ast.AssName(dst, 'OP_ASSIGN')],
                    ast.Name(src),
                ))
        if method.assigned_globals:
            assert isinstance(code, ast.Stmt)
            code.nodes.insert(0, ast.Global(method.assigned_globals.keys()))
        node = ast.Function(
            decorators,
            pyname,
            parameters,
            defaults,
            flags,
            self.parseComment(doc),
            code,
        )
        method.node = node
        node.locals = method.locals
        node.name = pyname
        #node.name = self.addLocal(name, node, None, None)
        self.method_stack.pop()
        self.method_var.pop()
        self.assigned_globals = save_assigned_globals
        return node

    def anonClass(self, ac, dst=None):
        cls = ac.cls_node
        cls.name = ac.cls_node.name
        if dst is None:
            anonvar = ac.cls_node.name
        elif isinstance(dst, basestring):
            anonvar = dst
        else:
            cls.name = anonvar = dst.name
        stmt = self.stmt([])
        if ac.this_suffixes:
            for jname, tmpname, names in ac.this_suffixes:
                assert len(names) == 2
                stmt.nodes.append(ast.Assign(
                    [ast.AssName(tmpname, 'OP_ASSIGN')],
                    ast.Name(self.method_var[-1]),
                ))
        stmt.nodes.append(cls)
        name = ast.Name(cls.name)
        if hasattr(ac, 'arguments'):
            args = [self.dispatch(a) for a in ac.arguments]
        else:
            args = []
        if anonvar.find('.') >= 0:
            ass = ast.AssAttr(
                ast.Name(anonvar.split('.')[0]),
                anonvar.split('.', 1)[1],
                'OP_ASSIGN',
            )
        else:
            self.addLocal(name.name, None, None, None)
            ass = ast.AssName(anonvar, 'OP_ASSIGN')
        stmt.nodes.append(
            ast.Assign(
                [ass],
                self.fixCallFunc(
                    ac.java_node,
                    name,
                    args,
                ),
            ),
        )
        return (stmt, name)

    def fixCallFunc(self, e, node, arguments=None, scoped=True):
        node_ast = ast.CallFunc
        if arguments is None:
            arguments = [self.dispatch(a) for a in e.arguments]
        if isinstance(node, basestring):
            if scoped:
                node = self.scoped(node, callable=True)
        elif isinstance(node, ast.Name):
            if scoped:
                n = node
                node = self.scoped(node.name, callable=True)
                node.comments = n.comments
        elif isinstance(node, AnonymousClass):
            raise AstError(e, "AnonymousClass")
        elif isinstance(node, ast.Getattr):
            attrname = node.attrname
            if isinstance(attrname, ast.Name):
                attrname = attrname.name
            if isinstance(node.expr, ast.Name) and \
               self.method_var and \
               node.expr.name == self.method_var[-1]:
                variable = self.getClassLocal(attrname)
                if variable is not None and \
                   variable.clashed and \
                   variable.name in self.methods[-1]:
                    attrname = self.methods[-1][variable.name].pyname
                    node.attrname = attrname
            if attrname in self.method_map:
                node, arguments, node_ast, ast_args = self.mapMethod(
                    node, attrname, arguments,
                )
                if arguments is None:
                    return node
        args = []
        stmt = self.stmt([])
        for a in arguments:
            if isinstance(a, jast.Node):
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
        elif node_ast is ast.Slice:
            node = node_ast(node, 'OP_APPLY', args[0], args[1])
        elif node_ast is ast.Compare:
            op = ast_args[0]
            if (
                op == '==' and
                len(args) == 1 and
                isinstance(args[0], ast.Const) and
                args[0].value is None
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
        def listCreator(nodes):
            _nodes = []
            for node in nodes:
                if isinstance(node, list):
                    node = listCreator(node)
                _nodes.append(node)
            return ast.List(_nodes)

        if e.initializer is not None:
            if isinstance(e.initializer, list):
                node = listCreator(self.dispatch_list(e.initializer))
            else:
                right = self.dispatch(e.initializer)
                node = ast.Mul((ast.List([ast.Name(None)]), right))
            return node
        return ast.EmptyNode()

    def visitAssert(self, e):
        test = self.dispatch(e.test)
        value = e.value
        if value is not None:
            value = self.dispatch(e.value)
        return ast.Assert(test, value)

    def visitAssignExpr(self, e):
        op = e.op
        dst = self.dispatch(e.left)
        if isinstance(dst, ast.Name) and dst.name in self.globals:
            if self.assigned_globals is not None:
                self.assigned_globals[dst.name] = True
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
                self.warning(e.lineno, 'Cannot handle assign expression (too complex)')
                stmt.comments = [
                    '%s (line %s): assign expression too complex' % (
                        fixme, e.lineno,
                    ),
                ]
                return stmt
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
        attrib_map = self.attrib_map
        if isinstance(e.node, jast.QualifiedIdentifier):
            self.attrib_map = {}
            node = self.dispatch(e.node, callable=True)
        elif isinstance(e.node, jast.Identifier):
            node = self.dispatch(e.node, callable=True)
        else:
            node = self.dispatch(e.node)
        self.attrib_map = attrib_map
        return self.fixCallFunc(e, node)

    def visitCastExpr(self, e):
        return self.dispatch(e.node)

    def visitClass(self, e):
        if self.getClassDepth() - self.opts.as_module <= 0:
            class_name = self.addGlobal(e.name, None, e, None)#, e.modifiers)
        else:
            class_name = self.addLocal(e.name, None, e, None)#, e.modifiers)
        bases = []
        if e.extends is not None:
            # Interface can have multiple extends
            if isinstance(e.extends, list):
                extends = e.extends
            else:
                extends = [e.extends]
            for extend in extends:
                base = self.dispatch(extend, test_globals=True)
                assert isinstance(base, Type)
                base = self.mapType(base.name)
                bases.append(base)
        if hasattr(e, 'implements') and e.implements is not None:
            for impl in e.implements:
                base = self.dispatch(impl, test_globals=True)
                assert isinstance(base, Type)
                base = self.mapType(base.name)
                bases.append(base)
        if not bases:
            bases = ['object']
        doc = self.parseComment(e.doc)
        stmt = self.stmt([])
        class_node = ast.Class(
            class_name,
            bases,
            doc,
            stmt,
            e.lineno
        )
        self.pushClassName(class_name, class_node)
        self.pushState()
        self.analysing += 1
        for n in e.members:
            self.append_to_class_init = False
            node = self.dispatch(n)
        state = self.popState()
        self.locals = state.locals
        if self.opts.add_get_package:
            if self.opts.as_module and self.getClassDepth() == 1:
                deco = None
                args = []
            else:
                deco = ast.Decorators([ast.Name('classmethod')])
                args = ['cls']
            stmt.nodes.append(ast.Function(
                deco,
                'getPackage',
                args,
                [],
                0,
                None,
                ast.Stmt([ast.Return(ast.Const(self.package_name))]),
            ))
        class_init = []
        init_method = self.getMethod('__init__')
        if init_method is not None:
            init_method.class_init = [ast.Stmt(class_init)]
        init_method_inserted_at = None
        for n in e.members:
            if init_method is None and isinstance(n, jast.Method):
                # Insert the __init__ method before all
                # other methods, but after some attibute
                # definitions (if any)
                # This method is inserted if there is some 
                # code to be inserted in the __init__ and
                # there's no __init__ defined.
                init_method = ast.Function(
                    None,
                    '__init__',
                    [
                        ast.Name('self'),
                        ast.Name('args'),
                        ast.Name('kwargs'),
                    ],
                    [],
                    ast.CO_VARARGS|ast.CO_VARKEYWORDS,
                    None,
                    ast.Stmt([ast.Stmt(class_init)]),
                )
                init_method.code.nodes.append(ast.CallFunc(
                    ast.Getattr(
                        ast.CallFunc(
                            ast.Name('super'),
                            [
                                ast.Name(self.getClassName()),
                                ast.Name('self'),
                            ],
                        ),
                        ast.Name('__init__'),
                    ),
                    [ast.Name('self')],
                    ast.Name('args'),
                    ast.Name('kwargs'),
                ))
                init_method_inserted_at = len(stmt.nodes)
                stmt.nodes.append(init_method)
            try:
                depth = self.getStackDepth()
                self.append_to_class_init = False
                node = self.dispatch(n)
                if self.append_to_class_init:
                    if isinstance(node, ast.Class):
                        pass
                    else:
                        assert isinstance(node, ast.Stmt)
                        assert len(node.nodes) == 1
                        assnode = node.nodes[0]
                        assert isinstance(assnode, ast.Assign)
                        assert len(assnode.nodes) == 1
                        dst = assnode.nodes[0]
                        assert isinstance(dst, ast.AssName)
                        assnode.nodes[0] = ast.AssAttr(
                            ast.Name('self'),
                            ast.Name(dst.name),
                            dst.flags,
                        )
                    class_init.append(node)
                    comments = node.comments
                    if comments:
                        if len(stmt.nodes) == 0:
                            stmt.nodes.append(ast.EmptyNode())
                        node = stmt.nodes[-1]
                        if not node.comments:
                            node.comments = comments
                        else:
                            node.comments += comments
                    continue
                if isinstance(node, list):
                    stmt.nodes += node
                elif isinstance(node, ast.Pass):
                    if node.comments:
                        stmt.nodes.append(node)
                else:
                    stmt.nodes.append(node)
            except IgnoreMethod:
                self.setStackDepth(depth)
        if init_method_inserted_at is not None and not class_init:
            del(stmt.nodes[init_method_inserted_at])
        if self.this_suffixes:
            if self.this_suffixes[-1][2][0] == class_name:
                self.this_suffixes.pop()
            #else:
            #    self.append_to_class_init = True
        self.flatten_stmt(stmt, True)
        cls = self.class_stack[-1]
        self.popClassName()
        if self.opts.as_module and self.getClassDepth() == 0:
            if doc:
                node = ast.EmptyNode()
                node.comments = [doc]
                stmt.nodes.insert(0, node)
            return stmt
        if cls.to_method:
            this_name = cls.move_to_method[0][0].name
            for mm in cls.move_to_method[1:]:
                assert mm[0].name == this_name
            this_name = '%s_this' % this_name
            code = ast.Stmt([
                class_node,
                ast.Return(
                    ast.CallFunc(
                        ast.Name(cls.name),
                        [],
                        ast.Name('args'),
                        ast.Name('kwargs'),
                    )
                ),
            ])
            class_node = ast.Function(
                None,
                cls.name,
                [
                    ast.Name(this_name),
                    ast.Name('args'),
                    ast.Name('kwargs'),
                ],
                [],
                ast.CO_VARARGS|ast.CO_VARKEYWORDS,
                None,
                code,
            )
            # Convert to statement and add an empty node
            # which will hold comments (if any) that are
            # supposed to come after the class definition
            return ast.Stmt(
                [class_node, ast.EmptyNode()],
            )
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
        cls_type = self.dispatch(e.type, test_globals=True)
        assert isinstance(cls_type, Type)
        cls_type = cls_type.name
        node = None
        if not e.nodes:
            node = self.fixCallFunc(
                e,
                ast.Name(cls_type),
                scoped=False,
            )
        self.addLocal(name, None, e, None)
        class_node = ast.Class(name, [cls_type], None, None)
        self.pushClassName(name, class_node)
        body = self.stmt(e.nodes, False)
        self.analysing = 0
        body = self.stmt(e.nodes, False)
        class_node.code = body
        self.popClassName()
        self.analysing = analysing
        _this_suffixes = self.this_suffixes
        self.this_suffixes = this_suffixes
        if node is not None:
            return node
        name, init = self.getTmpVar(None, 0)
        self.addLocal(name, None, e, None)
        return AnonymousClass(
            class_node,
            e,
            _this_suffixes,
        )

    def visitClassOrInterfaceType(self, e, test_globals=False):
        names = [t[0] for t in e.types]
        def find_clsname(names):
            for clsname in self.class_stack[0].node.bases:
                fpath = self.globals[clsname].imp_path
                g = file_globals.get(fpath)
                if g is not None:
                    for m in g[clsname].jtype.members:
                        if m.name == names[0]:
                            return clsname
            return clsname

        base = self.scoped(names[0], test_globals=test_globals, as_string=True)
        if base.startswith('SELF.'):
            if len(names) > 1:
                return Type('%s.%s' % (
                    self.addTypeImport('.'.join(names)), 
                    names[-1],
                ))
            if len(self.class_stack) > 0:
                clsname = find_clsname(names)
                base = "%s.%s" % (
                    clsname,
                    base[5:]
                )
        names[0] = base
        return Type('.'.join(names))

    def visitClassSuffix(self, e):
        node = self.dispatch(e.node)
        if not e.array:
            if isinstance(node, Type):
                node = ast.Name(node.name)
        else:
            node = ast.Name('list')
        return node

    def visitConditionalExpr(self, e):
        test = self.dispatch(e.test)
        then = self.dispatch(e.then)
        else_ = self.dispatch(e.else_)
        if isinstance(then, AnonymousClass):
            cls, name = self.anonClass(then)
            cls = then.cls_node
            cls.comments = [
                '%s AnonymousClass from line %s in conditional expr' % (
                    fixme, test.lineno,
                ),
            ]
            then = cls
            self.warning(test.lineno, 'Cannot handle anonymous classes in conditional expressions')
        if isinstance(else_, AnonymousClass):
            cls, name = self.anonClass(else_)
            cls = else_.cls_node
            cls.comments = [
                '%s AnonymousClass from line %s in conditional expr' % (
                    fixme, test.lineno,
                ),
            ]
            else_ = cls
            self.warning(test.lineno, 'Cannot handle anonymous classes in conditional expressions')
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
        node.comments_done = [e]
        if e.comments:
            node.comments = [
                [self.parseComment(c) for c in e.comments[0]],
                [],
                [self.parseComment(c) for c in e.comments[1]],
            ]
        nodes = [node]
        self.checkLabel(nodes)
        node = self.stmt(nodes)
        node.comments_done = [e]
        return node

    def visitEmptyNode(self, e):
        return ast.EmptyNode()

    def visitEnum(self, e):
        if self.getClassDepth() - self.opts.as_module <= 0:
            e.name = self.addGlobal(e.name, None, e, None)#, e.modifiers)
        else:
            e.name = self.addLocal(e.name, None, e, None)#, e.modifiers)
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
        self.pushClassName(e.name, class_node)
        self.pushState()
        self.analysing += 1
        self.enum_methods.append([])
        for n in e.members:
            self.dispatch(n)
        state = self.popState()
        self.locals = state.locals
        values = []
        for n in e.constants:
            node = self.dispatch(n)
            if not isinstance(node, ast.EmptyNode):
                values.append(ast.Name(node.nodes[0].name))
                stmt.nodes.append(node)
        hasConstructor = False
        for n in e.members:
            try:
                depth = self.getStackDepth()
                node = self.dispatch(n)
                if isinstance(n, jast.Method) and n.name == '__init__':
                    hasConstructor = True
                if isinstance(node, list):
                    stmt.nodes += node
                elif isinstance(node, ast.Pass):
                    if node.comments:
                        stmt.nodes.append(node)
                else:
                    stmt.nodes.append(node)
            except IgnoreMethod:
                self.setStackDepth(depth)
        stmt.nodes.append(ast.Assign(
            [ast.AssName('_values', 'OP_ASSIGN')],
            ast.List(values),
        ))
        stmt.nodes.append(ast.Function(
            ast.Decorators([ast.Name('classmethod')]),
            'values', ['cls'], [], 0, None,
            ast.Stmt([ast.Return(ast.Slice(
                ast.Getattr(ast.Name('cls'), self.enum_values_name),
                'OP_APPLY', None, None,
            ))],
        )))

        self.flatten_stmt(stmt, True)
        self.popClassName()
        if hasConstructor:
            nodes = [class_node]
            nodes.append(ast.Assign(
                [ast.AssAttr(
                    ast.Name(e.name),
                    self.enum_values_name,
                    'OP_ASSIGN',
                )],
                ast.ListComp(
                    ast.CallFunc(ast.Name(e.name), [], ast.Name('v'), None),
                    [ast.ListCompFor(
                        ast.AssName('v', 'OP_ASSIGN'),
                        ast.Getattr(ast.Name(e.name), self.enum_values_name),
                        [],
                    )],
                ),
            ))
            class_node = ast.Stmt(nodes)
        enum_methods = self.enum_methods.pop()
        if enum_methods:
            stmt = ast.Stmt([class_node])
            tmpvar = self.getTmpVar()[0]
            for mname, mnode in enum_methods:
                a = AnonymousClass(
                    ast.Class(tmpvar, [class_node.name], None, mnode),
                    e,
                    [],
                )
                self.addLocal(tmpvar, None, None, None)
                cls, a = self.anonClass(a, '%s.%s' % (class_node.name, mname))
                stmt.nodes.append(cls)
                stmt.nodes.append(ast.CallFunc(
                    ast.Getattr(
                        ast.Name(class_node.name),
                        ast.Getattr(
                            ast.Name('_values'),
                            'append',
                        ),
                    ),
                    [ast.Getattr(ast.Name(class_node.name), mname)],
                ))
            return stmt
        return class_node

    def visitEnumConstant(self, e):
        node = self.dispatch(e.name)
        name = e.name.name
        right = ast.Const(name)
        comments = None
        if len(e.nodes) > 0:
            cannot_handle = True
            if len(e.nodes) == 1 and isinstance(e.nodes[0], jast.Method):
                class_node = ast.EmptyNode()
                self.pushClassName('-', None)
                self.pushState()
                save_analysing = self.analysing
                self.analysing = 1
                meth = self.dispatch(e.nodes[0])
                self.analysing = 0
                meth = self.dispatch(e.nodes[0])
                self.analysing = save_analysing
                self.enum_methods[-1].append([name, meth])
                self.popState()
                self.popClassName()
                return class_node
            if cannot_handle:
                self.warning(e.lineno, 'Cannot handle nodes in enum constants')
                right.comments = ['%s: EnumConstant (line: %s)' % (
                    fixme, e.lineno,
                )]
        if not e.arguments:
            node = ast.Assign(
                [ast.AssName(name, 'OP_ASSIGN')],
                right,
            )
        else:
            args = self.dispatch_list(e.arguments)
            node = ast.Assign(
                [ast.AssName(name, 'OP_ASSIGN')],
                ast.List(args),
            )
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
                elif isinstance(init, list):
                    init = self.dispatch_list(init)
                    init = ast.List(init)
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
            a.nodes[0].name = self.addLocal(
                a.nodes[0].name, a.nodes[0], e.type, e.modifiers,
            )
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
            body = e.nodes
            if not isinstance(body, list):
                body = [body]
            body = self.stmt(body, True)
        # Attach comments from block top to an empty node
        empty = ast.EmptyNode()
        if e.comments:
            empty.comments = [self.parseComment(c) for c in e.comments[0]]
        else:
            e.comments = None
        empty.comments_done = [e]
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
                        ast.Not(compare),
                        ast.Break(),
                    )],
                    None,
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
        empty.comments_done = [e]
        # And append that to the statements
        stmt.nodes.append(empty)
        self.checkLabel(stmt.nodes)
        stmt.comments_done = [e]
        return stmt

    def visitForEach(self, e):
        name = e.var[1].name
        name = self.addLocal(name, None, e.var[0], None)
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
        node = self.stmt(nodes)
        node.comments_done = [e]
        return node

    def visitIdentifier(self, e, callable=False):
        if e.name == 'this':
            if self.localsIsClassLocals():
                node = ast.Name('self')
                self.append_to_class_init = True
                return node
            return ast.Name(self.method_var[-1])
        if e.name == 'super':
            self.block_self_scope = True
            node = ast.CallFunc(
                ast.Name('super'),
                [ast.Name(self.getClassName()), ast.Name(self.method_var[-1])],
            )
            return node
        name = self.scoped(e.name, callable=callable)
        return name

    def visitIf(self, e):
        test = self.dispatch(e.test)
        then = self.stmt(e.then, True)
        node = None
        comments = []
        comments_done = []
        if e.else_ is None:
            else_ = None
        else:
            else_ = self.stmt(e.else_, True)
            if isinstance(else_, ast.If):
                node = else_
                else_ = None
                node.tests.insert(0, (test, then))
            if isinstance(else_, ast.Stmt) and len(else_.nodes) == 1:
                if isinstance(else_.nodes[0], ast.If):
                    node = else_.nodes[0]
                    node.tests.insert(0, (test, then))
        if node is None:
            node = ast.If(
                [(test, then)],
                else_,
            )
            node.comments = []
            node.comments_done = []
        # Change comments from flat list to
        # (test, then)+ else rest
        node.comments_done.append(e)
        comments = e.comments[:]
        c1 = comments.pop(0)
        c1 = [self.parseComment(c) for c in c1]
        c2 = comments.pop(0)
        c2 = [self.parseComment(c) for c in c2]
        node.comments.insert(0, (c1, c2))
        while len(comments) > 0:
            c1 = comments.pop(0)
            c1 = [self.parseComment(c) for c in c1]
            node.comments.append(c1)
        return node

    def visitInnerCreator(self, e):
        left = self.dispatch(e.node)
        self.block_self_scope = True
        right = self.visitClassCreator(e)
        if isinstance(left, AnonymousClass):
            raise AstError(e, "AnonymousClass")
        node = ast.Getattr(left, right)
        return node

    def visitInstanceOfExpr(self, e):
        type_ = self.dispatch(e.type)
        assert isinstance(type_, Type)
        type_ = self.mapType(type_.name)
        comments = None
        if isinstance(e.type, jast.Type) and e.type.array:
            comments = ['Array %s of %s' % (''.join(e.type.array), type_)]
            type_ = 'list'
        node = self.fixCallFunc(
            e,
            ast.Name("isinstance"),
            [e.node, ast.Name(type_)],
            scoped=False,
        )
        if comments is not None:
            if node.comments is None:
                node.comments = comments
            else:
                node.comments += comments
        return node

    def visitInterface(self, e):
        return self.visitClass(e)

    def visitLabel(self, e):
        e.name = self.addLocal(e.name, None, None, None)
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
            elif isinstance(init, list):
                init = self.dispatch_list(init)
                init = ast.List(init)
            else:
                init = self.dispatch(init)
                if isinstance(init, AnonymousClass):
                    cls, name = self.anonClass(init, v)
                    cls = init.cls_node
                    cls.name = v.name
                    nodes.insert(0, cls)
                    continue
            if isinstance(init, AnonymousClass):
                raise AstError(e, "AnonymousClass")
            name = self.addLocal(name, None, e.type, e.modifiers)
            if init is not None:
                if isinstance(init, ast.Stmt):
                    nodes += init.nodes
                    init = nodes.pop()
                a = ast.Assign(
                    [ast.AssName(name, 'OP_ASSIGN')],
                    init,
                )
                nodes.append(a)
                name = self.addLocal(name, a.nodes[0], e.type, e.modifiers)
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

    def visitPrimitiveType(self, e, test_globals=False):
        return Type(e.name)

    def visitPostOperator(self, e):
        if e.op == '++':
            fname = 'POSTINC'
        else:
            fname = 'POSTDEC'
        self.addJavaLib(fname)
        args = [self.dispatch(e.node)]
        return self.libCallGlobalsAndLocals(fname, args)

    def visitQualifiedIdentifier(self, e, callable=False):
        names = self.mapQualifiedName(e.name).split('.')
        if names[0] == 'this':
            names[0] = self.method_var[-1]
            if self.hasClassLocal(names[1]):
                names[1] = self.getClassLocal(names[1]).pyname
        if names[-1] == 'this':
            assert len(names) == 2
            class_name = names[0]
            idx = self.class_names.index(class_name)
            name = '_'.join(names)
            value = (e.name, name, names)
            if not value in self.this_suffixes:
                self.this_suffixes.append(value)
            node = ast.Name(name)
            node.class_locals = self.locals[self.class_locals[idx]]
            return node
        issuper = names[0] == 'super'
        if issuper:
            raise AstError(e, 'issuper')
            names.pop(0)
        node = self.scoped('.'.join(names), callable=callable)
        if issuper:
            node = self.fixCallFunc(
                e,
                ast.CallFunc(
                    ast.Name('super'),
                    [ast.Name(self.getClassName()), ast.Name(self.method_var[-1])],
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
        if isinstance(right, ast.Name) and \
           hasattr(left, 'class_locals') and \
           right.name in left.class_locals:
            right.name = left.class_locals[right.name].pyname
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
                [ast.Name(self.getClassName()), ast.Name(self.method_var[-1])],
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

    def visitSynchronized(self, e):
        nodes = [self.dispatch(n) for n in e.nodes]
        return self.stmt(nodes)

    def visitThis(self, e):
        node = self.fixCallFunc(
            e,
            ast.Getattr(ast.Name(self.method_var[-1]), '__init__'),
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
            elif isinstance(type_, Type):
                type_ = ast.Name(type_.name)
            catch[1].name = self.addLocal(catch[1].name, None, type_, None)
            name = self.dispatch(catch[1])
            node = self.stmt(catch[2])
            handlers.append((type_, name, node))
        node = ast.TryExcept(try_block, handlers, None)
        finally_block = None
        if e.finally_ is not None:
            finally_block = self.stmt(e.finally_)
            node = ast.TryFinally(node, finally_block)
        return node

    def visitType(self, e, test_globals=False):
        return self.dispatch(e.type, test_globals=test_globals)

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
            node = ast.Pass()
            name = self.addLocal(name, node, e.type, None)
        else:
            init = self.dispatch(init)
            assert init is not None
            if isinstance(init, AnonymousClass):
                cls, name = self.anonClass(init, ast.Name(name))
                name = self.addLocal(name, cls, e.type, None)
                return cls
            node = ast.Assign(
                    [ast.AssName(name, 'OP_ASSIGN')],
                    init,
            )
            name = self.addLocal(name, node.nodes[0], e.type, None)
        return node

    def visitWhile(self, e):
        test = self.dispatch(e.test)
        if test is None:
            raise AstError(s, "No condition")
        body = self.stmt(e.nodes)
        node = ast.While(test,body,None)
        node.comments_done = [e]
        if e.comments:
            node.comments = [
                [self.parseComment(c) for c in e.comments[0]],
                [],
                [self.parseComment(c) for c in e.comments[1]],
            ]
        nodes = [node]
        self.checkLabel(nodes)
        node = self.stmt(nodes)
        node.comments_done = [e]
        return node

    def visitUnaryExpr(self, e):
        expr = self.getOperatorExpr(e, e.op, e.node, None)
        if expr is not None:
            return expr
        raise AstError(e, "visitUnaryExpr operator '%s'" % op)

    # BOTTOM
