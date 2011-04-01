#!/usr/bin/env jython

import JavaAst as ast
import JavaTokens as tok
from fixes import *


class JavaAstError(Exception):
    pass


class Children(list):

    def iteritems(self):
        # Donot use yield (jython limitation)
        return [(i, i.getType(), i.getText()) for i in self]


class JavaWalker(object):

    literals = [
        tok.INTLITERAL,
        tok.LONGLITERAL,
        tok.FLOATLITERAL,
        tok.DOUBLELITERAL,
        tok.CHARLITERAL,
        tok.STRINGLITERAL,
        tok.TRUE,
        tok.FALSE,
        tok.NULL,
    ]

    modifiers = [
        'public',
        'protected',
        'private',
        'static',
        'abstract',
        'final',
        'native',
        'synchronized',
        'transient',
        'volatile',
        'strictfp',
    ]
    modifier_types = dict([(tok.strings[i], i) for i in modifiers])

    primitives = [
        'boolean',
        'char',
        'byte',
        'short',
        'int',
        'long',
        'float',
        'double',
    ]
    primitive_types = dict([(tok.strings[i], i) for i in primitives])


    def walk(self, tree, tokens=None):
        self.stack = []
        children = self.token_children(tree)
        self.comments = []
        self.javadocs = []
        self.tokens = tokens
        if tokens is not None:
            for i in range(tokens.size()):
                t = tokens.get(i)
                if t.getChannel() == t.HIDDEN_CHANNEL:
                    if t.getText().startswith("/**"):
                        self.javadocs.append(t)
                    else:
                        self.comments.append(t)

        annotations = []
        package = None
        imports = []
        types = []
        for c, type_, text in children.iteritems():
            if type_ == tok.PACKAGE:
                package = self.dispatch(c)
            elif type_ == tok.IMPORT:
                imports.append(self.dispatch(c))
            elif type_ == tok.ClassDeclaration:
                types.append(self.dispatch(c))
            elif type_ == tok.EnumDeclaration:
                types.append(self.dispatch(c))
            elif type_ == tok.InterfaceDeclaration:
                types.append(self.dispatch(c))
            elif type_ == tok.Annotations:
                annotations = self.dispatch(c)
            elif type_ == tok.AnnotationTypeDeclaration:
                annotations = self.dispatch(c)
            else:
                self.unknown_token(c)
        node = self.node(
            tree, ast.CompilationUnit,
            annotations, package, imports, types,
        )
        self.attachComment(node, None)
        return node

    def pop(self):
        t = self.stack.pop()
        return t

    def push(self, t):
        self.stack.append(t)

    def node(self, token, node, *args, **kwargs):
        n = node(*args, **kwargs)
        n.lineno = token.getLine()
        n.charpos = token.getCharPositionInLine()
        return n

    def unknown_token(self, token):
        raise JavaAstError("Unknown token %r, %s (line %d:%d)" % (
            token, 
            token.getType(),
            token.getLine(),
            token.getCharPositionInLine(),
        ))

    def dispatch(self, token):
        node = None
        text = token.getText()
        method = "walk_%s" % text
        if hasattr(token, 'getChildren'):
            children = self.token_children(token)
        else:
            children = None
        if hasattr(self, method):
            if children is None:
                node = getattr(self, method)(token)
            else:
                node = getattr(self, method)(token, children)
        else:
            type_ = token.getType()
            if type_ == tok.IDENTIFIER:
                node = self.walk_identifier(token, children)
            elif type_ in self.literals:
                node = self.walk_literal(token)
        if node is not None:
            if isinstance(node, ast.Node):
                self.attachComment(node, token)
            return node
        raise ValueError(
            "Unhandled token type %s %r (%s:%s)" % (
                token.getType(),
                token,
                token.getLine(),
                token.getCharPositionInLine(),
            )
        )

    def assert_equal(self, left, right):
        if left != right:
            assert left == right, "%r == %r" % (left, right)

    def token_children(self, token):
        if token.getChildCount() == 0:
            return Children()
        if token.getChildCount() == 1:
            return Children([token.getChild(0)])
        return Children(token.getChildren())

    def token_children_text(self, token):
        if token.getChildCount() == 0:
            return []
        return [c.getText() for c in token.getChildren()]

    def left_op_right(self, token, children, cls):
        left = self.pop()
        assert len(children) >= 2, "%s %r" % (cls.__name__, children)
        idx = 1
        op = children[0].getText()
        if children[1].getText() in ['=', '<', '>']:
            op = "%s%s" % (op, children[1].getText())
            idx += 1
            if children[2].getText() == '>':
                op = "%s%s" % (op, children[2].getText())
                idx += 1
        right = self.dispatch(children[idx])
        children = children[idx + 1:]
        while children:
            self.push(right)
            right = self.dispatch(children.pop(0))
        return self.node(token, cls, left, op, right)

    def getJavaDoc(self, token):
        t = token.getToken()
        idx = token.getTokenStartIndex()
        while idx > 0:
            idx -= 1
            t = self.tokens.get(idx)
            if t.getChannel() != t.HIDDEN_CHANNEL:
                break
            text = t.getText()
            if text[:3] == '/**':
                return text
        return None

    def retrieveComment(self, token, start=None, stop=None, trailing=True):
        comments = []
        if token is None:
            idx = -1
            end = self.tokens.size() - 2
        else:
            idx = token.getTokenStartIndex()
            end = token.getTokenStopIndex()
            if idx == -1 and end == -1:
                return None
        if start:
            i = start.getTokenStartIndex()
            if i >= 0:
                idx = i
        if stop:
            i = stop.getTokenStopIndex()
            if i >= 0:
                end = i
        while True:
            idx += 1
            t = self.tokens.get(idx)
            if t.getChannel() != t.HIDDEN_CHANNEL:
                if idx > end:
                    break
                continue
            if not trailing and idx > end:
                break
            if not t in self.comments:
                continue
            text = t.getText()
            if text[:3] != '/**':
                comments.append(text)
            self.comments.remove(t)
        return comments

    def attachComment(self, node, token, start=None, stop=None):
        comments = self.retrieveComment(token, start, stop)
        if comments is not None and len(comments) > 0:
            if node.comments is None:
                node.comments = []
            node.comments += comments
        return node

    def addComments(self, node, start_token, end_token=None):
        return node
        if node.comments is None:
            node.comments = []
        idx = start_token.getToken().getTokenIndex()
        if end_token is None:
            end = self.tokens.size()
        else:
            end = end_token.getToken().getTokenIndex()
        while idx < end - 1:
            idx += 1
            t = self.tokens.get(idx)
            if t.getChannel() != t.HIDDEN_CHANNEL:
                if end_token is None:
                    continue
                break
            if not t in self.comments:
                continue
            text = t.getText()
            if text[:3] != '/**':
                node.comments.append(text)
            self.comments.remove(t)
        return node

    def flatten_expr(self, token, children):
        if len(children) == 0:
            return None
        stack_size = len(self.stack)
        childs = children[:]
        node = self.dispatch(childs.pop(0))
        while childs:
            self.push(node)
            node = self.dispatch(childs.pop(0))
        assert len(self.stack) == stack_size
        return node


    def walk_AdditiveExpression(self, token, children):
        return self.left_op_right(token, children, ast.AdditiveExpr)

    def walk_Annotation(self, token, children):
        name = None
        arguments = []
        for c in children:
            if name is None:
                name = self.dispatch(c)
            else:
                arguments += self.dispatch(c)
        return self.node(token, ast.Annotation, name, arguments)

    def walk_Annotations(self, token, children):
        annotations = []
        for c, type_, text in children.iteritems():
            if type_ == tok.Annotation:
                annotations.append(self.dispatch(c))
            else:
                self.unknown_token(c)
        return annotations

    def walk_AnnotationArgument(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_AnnotationTypeBody(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_AnnotationTypeDeclaration(self, token, children):
        modifiers = []
        name = None
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = self.dispatch(c)
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif type_ == tok.AnnotationTypeBody:
                nodes = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.AnnotationType, name, nodes, modifiers)

    def walk_AnnotationMethodDeclaration(self, token, children):
        name = None
        annon_type = None
        modifiers = []
        elem = None
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = self.dispatch(c)
            elif type_ == tok.Type:
                annon_type = self.dispatch(c)
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif elem is None:
                elem = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(
            token, ast.AnnotationMethod, name, annon_type, elem, modifiers,
        )

    def walk_Arguments(self, token, children):
        nodes = []
        for c in children:
            nodes.append(self.dispatch(c))
        return nodes

    def walk_ArrayAccess(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_ArrayCreator(self, token, children):
        name = None
        array = []
        initializer = None
        for c, type_, text in children.iteritems():
            if name is None:
                name = self.dispatch(c)
            elif type_ == tok.LBRACKET:
                array.append('')
            elif type_ == tok.Expression:
                expr = self.dispatch(c)
                if array[0] == '':
                    initializer = expr
                else:
                    array[-1] = expr
            elif type_ == tok.ArrayInitializer:
                if initializer is None:
                    initializer = []
                initializer += self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.ArrayCreator, name, array, initializer)

    def walk_ArrayInitializer(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_assert(self, token, children):
        test = None
        value = None
        for c, type_, text in children.iteritems():
            if test is None and type_ == tok.Expression:
                test = self.dispatch(c)
            elif value is None and type_ == tok.Expression:
                value = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.Assert, test, value)
        
    def walk_AssignExpr(self, token, children):
        return self.left_op_right(token, children, ast.AssignExpr)

    def walk_BitAndExpression(self, token, children):
        return self.left_op_right(token, children, ast.BitAndExpr)

    def walk_BitOrExpression(self, token, children):
        return self.left_op_right(token, children, ast.BitOrExpr)

    def walk_BitXorExpression(self, token, children):
        return self.left_op_right(token, children, ast.BitXorExpr)

    def walk_Block(self, token, children):
        nodes = []
        last_c = None
        final = False
        for c, type_, text in children.iteritems():
            if type_ == tok.FINAL:
                final = True
                continue
            node = self.dispatch(c)
            if final:
                node.final = True
            final = False
            self.addComments(node, c)
            nodes.append(node)
            if last_c is not None:
                self.addComments(last_node, last_c, c)
            last_c = c
            last_node = node
        return nodes

    def walk_break(self, token, children):
        assert len(children) < 2
        label = None
        if len(children) == 1:
            label = children[0].getText()
        return self.node(token, ast.Break, label)

    def walk_catch(self, token, children):
        catch_type = None
        catch_var = None
        catch_block = []
        for c, type_, text in children.iteritems():
            if type_ == tok.Type:
                catch_type = self.dispatch(c)
            elif type_ == tok.IDENTIFIER:
                catch_var = self.dispatch(c)
            elif type_ == tok.Block:
                catch_block = self.dispatch(c)
            else:
                self.unknown_token(c)
        return (catch_type, catch_var, catch_block)


    def walk_CastExpression(self, token, children):
        casttype = None
        node = None
        for c, type_, text in children.iteritems():
            if casttype is None and type_ == tok.Type:
                casttype = self.dispatch(c)
            elif casttype is None and type_ == tok.PrimitiveType:
                casttype = self.dispatch(c)
            elif node is None and type_ != tok.Type:
                node = self.dispatch(c)
            elif type_ == tok.Selector:
                self.push(node)
                node = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.CastExpr, node, casttype)

    def walk_ClassBlock(self, token, children):
        modifiers = []
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.Block:
                nodes += self.dispatch(c)
            elif type_ == tok.STATIC:
                modifiers.append(text)
            else:
                self.unknown_token(c)
        return self.node(token, ast.ClassBlock, nodes, modifiers)

    def walk_ClassBody(self, token, children):
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.MethodDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.FieldDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.ClassDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.InterfaceDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.ClassBlock:
                nodes.append(self.dispatch(c))
            elif type_ == tok.EnumDeclaration:
                nodes.append(self.dispatch(c))
            else:
                self.unknown_token(c)
        return nodes

    def walk_ClassCreator(self, token, children):
        cls_type = None
        arguments = None
        typeArguments = None
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.ClassOrInterfaceType:
                cls_type = self.dispatch(c)
            elif type_ == tok.Arguments:
                arguments = self.dispatch(c)
            elif type_ == tok.ClassBody:
                nodes += self.dispatch(c)
            elif type_ == tok.IDENTIFIER:
                # innerCreator
                types = [text, None]
                cls_type = self.node(c, ast.ClassOrInterfaceType, types)
            else:
                self.unknown_token(c)
        return self.node(
            token, ast.ClassCreator, cls_type, typeArguments, arguments, nodes,
        )

    def walk_ClassDeclaration(self, token, children):
        name = None
        modifiers = []
        types = []
        extends = None
        implements = []
        nodes = []
        doc = self.getJavaDoc(token)
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = text
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif type_ == tok.EXTENDS:
                extends = self.dispatch(c)
            elif type_ == tok.IMPLEMENTS:
                implements = self.dispatch(c)
            elif type_ == tok.TypeParameters:
                types = self.dispatch(c)
            elif type_ == tok.ClassBody:
                nodes = self.dispatch(c)
            else:
                self.unknown_token(c)
        node = self.node(
            token, ast.Class, name, doc, nodes, types, 
            extends, implements, modifiers,
        )
        if len(children) is None:
            last = None
        else:
            last = children[-1]
        self.addComments(node, token, last)
        return node

    def walk_ClassOrInterfaceType(self, token, children):
        types = []
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                types.append([text, None])
            elif type_ == tok.TypeArguments:
                types[-1][1] = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.ClassOrInterfaceType, types)

    def walk_ConditionalExpression(self, token, children):
        test = self.pop()
        assert len(children) >= 2, "%s %r" % (cls.__name__, children)
        then = self.dispatch(children[0])
        else_ = self.dispatch(children[1])
        children = children[2:]
        while children:
            self.push(else_)
            else_ = self.dispatch(children.pop(0))
        return self.node(token, ast.ConditionalExpr, test, then, else_)

    def walk_ConditionalAndExpression(self, token, children):
        return self.left_op_right(token, children, ast.AndExpr)

    def walk_ConditionalOrExpression(self, token, children):
        return self.left_op_right(token, children, ast.OrExpr)

    def walk_continue(self, token, children):
        assert len(children) < 2
        label = None
        if len(children) == 1:
            label = children[0].getText()
        return self.node(token, ast.Continue, label)

    def walk_do(self, token, children):
        test = None
        node = None
        comment_list = []
        for c, type_, text in children.iteritems():
            if node is None:
                node = self.dispatch(c)
            elif test is None and type_ == tok.ParExpression:
                test = self.dispatch(c)
            else:
                self.unknown_token(c)
        comment_list.append(self.retrieveComment(token, trailing=False))
        comment_list.append(self.retrieveComment(token))
        node = self.node(token, ast.DoWhile, test, node)
        node.comments = comment_list
        return node

    def walk_else(self, token, children):
        nodes = []
        for c, type_, text in children.iteritems():
            node = self.dispatch(c)
            if isinstance(node, ast.Node):
                nodes.append(node)
            else:
                nodes += node
        return nodes

    def walk_ElementValueArrayInitializer(self, token, children):
        elements = []
        for c in children:
            elements.append(self.dispatch(c))
        return elements

    def walk_EnhancedForExpr(self, token, children):
        assert len(children) == 1
        return self.dispatch(children[0])

    def walk_EnhancedForVar(self, token, children):
        assert len(children) == 2
        var_type = self.dispatch(children[0])
        var_name = self.dispatch(children[1])
        return (var_type, var_name)

    def walk_EnumBody(self, token, children):
        nodes = []
        constants = []
        for c, type_, text in children.iteritems():
            if type_ == tok.EnumConstant:
                constants.append(self.dispatch(c))
            elif type_ == tok.MethodDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.FieldDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.ClassDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.InterfaceDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.ClassBlock:
                nodes.append(self.dispatch(c))
            elif type_ == tok.EnumDeclaration:
                nodes.append(self.dispatch(c))
            else:
                self.unknown_token(c)
        return nodes, constants

    def walk_EnumConstant(self, token, children):
        annotations = []
        name = None
        arguments = []
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.Annotations:
                annotations = self.dispatch(c)
            elif type_ == tok.IDENTIFIER:
                name = self.dispatch(c)
            elif type_ == tok.Arguments:
                arguments = self.dispatch(c)
            elif type_ == tok.ClassBody:
                nodes += self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(
            token, ast.EnumConstant, name, annotations, arguments, nodes,
        )

    def walk_EnumDeclaration(self, token, children):
        name = None
        modifiers = []
        types = []
        implements = []
        nodes = []
        constants = None
        doc = self.getJavaDoc(token)
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = text
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif type_ == tok.IMPLEMENTS:
                implements = self.dispatch(c)
            elif type_ == tok.EnumBody:
                nodes, constants = self.dispatch(c)
            else:
                self.unknown_token(c)
        node = self.node(
            token, ast.Enum, name, doc, nodes, constants, 
            implements, modifiers,
        )
        if len(children) is None:
            last = None
        else:
            last = children[-1]
        self.addComments(node, token, last)
        return node

    def walk_EqualityExpression(self, token, children):
        return self.left_op_right(token, children, ast.EqualityExpr)

    def walk_Expression(self, token, children):
        return self.flatten_expr(token, children)

    def walk_extends(self, token, children):
        assert len(children) > 0
        if len(children) > 1:
            return [self.dispatch(c) for c in children]
        return self.dispatch(children[0])

    def walk_FieldDeclaration(self, token, children):
        nodes = []
        modifiers = None
        decltype = None
        for c, type_, text in children.iteritems():
            if type_ == tok.VariableDeclarator:
                nodes.append(self.dispatch(c))
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif type_ == tok.Type:
                decltype = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(
            token, ast.FieldDeclaration, nodes, decltype, modifiers,
        )

    def walk_finally(self, token, children):
        assert len(children) == 1
        return self.dispatch(children[0])

    def walk_for(self, token, children):
        init = None
        expr = None
        update = None
        nodes = []
        enhanced = False
        for c, type_, text in children.iteritems():
            if type_ == tok.EnhancedForVar:
                init = self.dispatch(c)
                enhanced = True
            elif type_ == tok.EnhancedForExpr:
                expr = self.dispatch(c)
            elif type_ == tok.Statement:
                nodes = self.dispatch(c)
            elif type_ == tok.ForInit:
                init = self.dispatch(c)
            elif type_ == tok.ForExpr:
                expr = self.dispatch(c)
            elif type_ == tok.ForUpdate:
                update = self.dispatch(c)
            else:
                self.unknown_token(c)
        # Two ranges of comments. One just after the for statement
        # (begining of block) and one after finishing the for statement
        # (after the for block)
        comment_list = []
        comment_list.append(self.retrieveComment(token, trailing=False))
        comment_list.append(self.retrieveComment(token))
        if enhanced:
            node = self.node(
                token, ast.ForEach, init, expr, nodes
            )
        else:
            node = self.node(
                token, ast.For, init, expr, update, nodes
            )
        node.comments = comment_list
        return node

    def walk_ForExpr(self, token, children):
        assert len(children) == 1
        return self.dispatch(children[0])

    def walk_ForInit(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_FormalParameters(self, token, children):
        nodes = []
        for c in children:
            nodes.append(self.dispatch(c))
        return nodes

    def walk_ForUpdate(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_identifier(self, token, children):
        suffix = None
        if not children:
            text = token.getText()
            return self.node(token, ast.Identifier, text, suffix)
        name = [token.getText()]
        n = 0
        for c, type_, text in children.iteritems():
            n += 1
            if type_ == tok.IDENTIFIER:
                name.append(text)
            elif type_ == tok.THIS:
                name.append(text)
            else:
                n -= 1
                break
        if len(name) == 1:
            node = self.node(token, ast.Identifier, name[0], suffix)
        else:
            name = '.'.join(name)
            node = self.node(token, ast.QualifiedIdentifier, name, suffix)
        if n == len(children):
            return node
        assert n == len(children) - 1, "identifier: %s %r" % (n, children)
        #c = children[n]
        #type_, text = c.getType(), c.getText()
        for c, type_, text in Children(children[n:]).iteritems():
            if type_ == tok.ArrayAccess:
                array = self.dispatch(c)
                node = self.node(token, ast.ArrayAccess, node, array)
            elif type_ == tok.CLASS:
                node.suffix = 'class'
            elif type_ == tok.Arguments:
                arguments = self.dispatch(c)
                node = self.node(token, ast.CallFunc, node, arguments)
            elif type_ == tok.TypedSuffix:
                self.push(node)
                node = self.dispatch(c)
            elif type_ == tok.IdentifierSuffixClass:
                self.push(node)
                node = self.dispatch(c)
            elif type_ == tok.InnerCreator:
                self.push(node)
                node = self.dispatch(c)
            else:
                self.unknown_token(c)
        #raise JavaAstError(token, "Invalid identifier %r, %r:%r" % (
        #    token, c, type_))
        return node

    def walk_IdentifierSuffixClass(self, token, children):
        array = []
        for c in children:
            array.append('[]')
        node = self.pop()
        return self.node(token, ast.ClassSuffix, node, array)

    def walk_if(self, token, children):
        test = None
        then = None
        else_ = None
        comment_list = []
        prev_c = None
        for c, type_, text in children.iteritems():
            if type_ == tok.ELSE:
                else_ = self.dispatch(c)
            elif test is None:
                test = self.dispatch(c)
            elif then is None:
                then = self.dispatch(c)
            else:
                self.unknown_token(c)
            if prev_c is not None:
                comment_list.append(self.retrieveComment(token, None, prev_c))
            prev_c = c
        if isinstance(then, ast.Node):
            then = [then]
        if isinstance(else_, ast.Node):
            else_ = [else_]
        comment_list.append(self.retrieveComment(token, trailing=False))
        comment_list.append(self.retrieveComment(token))
        node = self.node(token, ast.If, test, then, else_)
        node.comments = comment_list
        return node

    def walk_implements(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_import(self, token, children):
        name = []
        wildcard = None
        modifiers = None
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name.append(text)
            elif type_ == tok.STAR:
                wildcard = '*'
            elif type_ == tok.STATIC:
                modifiers = ['static']
            else:
                self.unknown_token(c)
        name = '.'.join(name)
        return self.node(token, ast.Import, name, wildcard, modifiers)

    def walk_InnerCreator(self, token, children):
        cls_type = None
        arguments = None
        typeArguments = None
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                types = [[text, None]]
                cls_type = self.node(c, ast.ClassOrInterfaceType, types)
            elif type_ == tok.Arguments:
                arguments = self.dispatch(c)
            elif type_ == tok.ClassBody:
                nodes += self.dispatch(c)
            else:
                self.unknown_token(c)
        left = self.pop()
        return self.node(
            token, ast.InnerCreator, left, cls_type, typeArguments, arguments, nodes,
        )

    def walk_instanceof(self, token, children):
        left = self.pop()
        assert len(children) == 1
        right = self.dispatch(children[0])
        return self.node(token, ast.InstanceOfExpr, left, right)

    def walk_InterfaceBody(self, token, children):
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.AnnotationTypeDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.InterfaceMethodDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.InterfaceFieldDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.ClassDeclaration:
                nodes.append(self.dispatch(c))
            elif type_ == tok.InterfaceDeclaration:
                nodes.append(self.dispatch(c))
            #elif type_ == tok.ClassBlock:
            #    nodes.append(self.dispatch(c))
            elif type_ == tok.EnumDeclaration:
                nodes.append(self.dispatch(c))
            else:
                self.unknown_token(c)
        return nodes

    def walk_InterfaceDeclaration(self, token, children):
        name = None
        modifiers = []
        types = []
        extends = None
        nodes = []
        doc = self.getJavaDoc(token)
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = text
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif type_ == tok.EXTENDS:
                extends = self.dispatch(c)
            elif type_ == tok.TypeParameters:
                types = self.dispatch(c)
            elif type_ == tok.InterfaceBody:
                nodes = self.dispatch(c)
            else:
                self.unknown_token(c)
        node = self.node(
            token, ast.Interface, name, doc, nodes, types, 
            extends, modifiers,
        )
        if len(children) is None:
            last = None
        else:
            last = children[-1]
        self.addComments(node, token, last)
        return node

    def walk_InterfaceFieldDeclaration(self, token, children):
        return self.walk_FieldDeclaration(token, children)

    def walk_InterfaceMethodDeclaration(self, token, children):
        return self.walk_MethodDeclaration(token, children)

    def walk_Label(self, token, children):
        name = None
        node = None
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = text
            elif type_ == tok.Statement:
                node = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.Label, name, node)

    def walk_literal(self, token):
        type_ = token.getType()
        value = token.getText()
        if type_ == tok.INTLITERAL:
            if value[:2] == '0x':
                value = int(value, 16)
            else:
                value = int(value)
            type_ = 'int'
        elif type_ == tok.LONGLITERAL:
            value = long(value)
            type_ = 'long'
        elif type_ == tok.FLOATLITERAL:
            if value[-1] in ['F', 'f']:
                value = float(value[:-1])
            else:
                value = float(value)
            type_ = 'float'
        elif type_ == tok.DOUBLELITERAL:
            if value[-1] in ['D', 'd']:
                value = float(value[:-1])
            else:
                value = float(value)
            type_ = 'double'
        elif type_ == tok.CHARLITERAL:
            value = value[1:-1]
            type_ = 'char'
        elif type_ == tok.STRINGLITERAL:
            value = value[1:-1]
            type_ = 'string'
        elif type_ == tok.TRUE:
            value = True
            type_ = 'boolean'
        elif type_ == tok.FALSE:
            value = False
            type_ = 'boolean'
        elif type_ == tok.NULL:
            value = None
            type_ = 'null'
        else:
            raise JavaAstError(token, "Invalid literal %r" % token)
        return self.node(token, ast.Literal, value, type_)

    def walk_LocalVariableDeclaration(self, token, children):
        nodes = []
        modifiers = None
        decltype = None
        for c, type_, text in children.iteritems():
            if type_ == tok.VariableDeclarator:
                nodes.append(self.dispatch(c))
            elif type_ == tok.Modifiers:
                if modifiers is None:
                    modifiers = []
                modifiers += self.dispatch(c)
            elif type_ == tok.Type:
                decltype = self.dispatch(c)
            elif type_ == tok.FINAL:
                if modifiers is None:
                    modifiers = []
                modifiers.append(text)
            elif type_ == tok.Annotation:
                if modifiers is None:
                    modifiers = []
                modifiers.append(self.dispatch(c))
            else:
                self.unknown_token(c)
        return self.node(
            token, ast.LocalVariableDeclaration, nodes, decltype, modifiers,
        )

    def walk_MethodDeclaration(self, token, children):
        name = None
        nodes = []
        parameters = []
        modifiers = []
        types = []
        throws = None
        doc = self.getJavaDoc(token)
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = c.getText()
            elif type_ == tok.Modifiers:
                modifiers = self.dispatch(c)
            elif type_ == tok.Type:
                types = self.dispatch(c)
            elif type_ == tok.FormalParameters:
                parameters = self.dispatch(c)
            elif type_ == tok.Block:
                nodes += self.dispatch(c)
            elif type_ == tok.SEMI:
                # Empty block
                pass
            elif type_ == tok.TypeParameters:
                parameters = self.dispatch(c)
            elif type_ == tok.THROWS:
                throws = self.dispatch(c)
            else:
                self.unknown_token(c)
        
        return self.node(
            token, ast.Method, name, doc, nodes, parameters, 
            types, modifiers, throws,
        )

    def walk_Modifiers(self, token, children):
        return self.token_children_text(token)

    def walk_MultiplicativeExpression(self, token, children):
        return self.left_op_right(token, children, ast.MultiplicativeExpr)

    def walk_package(self, token, children):
        name = "".join([c.getText() for c in children])
        return self.node(token, ast.Package, name)

    def walk_ParameterDecl(self, token, children):
        name = None
        modifiers = []
        vartype = None
        type_modifiers = []
        for c, type_, text in children.iteritems():
            if type_ == tok.IDENTIFIER:
                name = text
            elif type_ in self.modifier_types:
                modifiers.append(text)
            elif type_ == tok.Type:
                assert vartype is None
                vartype = self.dispatch(c)
            elif type_ == tok.LBRACKET:
                type_modifiers.append('[]')
            elif type_ == tok.RBRACKET:
                # Ignore ']'
                pass
            elif type_ == tok.ELLIPSIS:
                type_modifiers.append('...')
            elif type_ == tok.Annotation:
                # Ignore Annotation
                self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(
            token, ast.ParameterDecl, name, vartype, 
            type_modifiers, modifiers,
        )

    def walk_ParExpression(self, token, children):
        assert len(children) == 1
        node = self.dispatch(children[0])
        return node
        return self.node(token, ast.ParExpr, node)

    def walk_PostOperator(self, token, children):
        assert len(children) == 1
        node = self.pop()
        return self.node(token, ast.PostOperator, node, children[0].text)

    def walk_PrimitiveType(self, token, children):
        assert len(children) == 1
        name = children[0].text
        return self.node(token, ast.PrimitiveType, name)

    def walk_PrimitiveTypeClass(self, token, children):
        array = []
        name = children[0].text
        for c in children[1:]:
            array.append('[]')
        node = self.node(token, ast.PrimitiveType, name)
        return self.node(token, ast.ClassSuffix, node, array)

    def walk_QualifiedName(self, token, children):
        return '.'.join([c.getText() for c in children])

    def walk_RelationalExpression(self, token, children):
        return self.left_op_right(token, children, ast.RelationalExpr)

    def walk_return(self, token, children):
        expr = self.flatten_expr(token, children)
        return self.node(token, ast.Return, expr)

    def walk_Selector(self, token, children):
        left = self.pop()
        right = None
        node = None
        types = None
        arguments = None
        for c, type_, text in children.iteritems():
            if right is None:
                if type_ == tok.TypeList:
                    assert types is None
                    types = self.dispatch(c)
                elif type_ == tok.ArrayAccess:
                    right = self.dispatch(c)
                    node = self.node(token, ast.ArrayAccess, left, right)
                else:
                    right = self.dispatch(c)
            elif type_ == tok.Arguments:
                arguments = self.dispatch(c)
            else:
                self.unknown_token(c)
        if node is None:
            node = self.node(token, ast.Selector, left, right, types)
        assert right is not None
        if arguments is not None:
            return self.node(token, ast.CallFunc, node, arguments)
        return node

    def walk_ShiftExpression(self, token, children):
        return self.left_op_right(token, children, ast.ShiftExpr)

    def walk_Statement(self, token, children):
        node = self.flatten_expr(token, children)
        if node is None:
            node = ast.EmptyNode()
        if isinstance(node, ast.Node):
            self.addComments(node, token)
        return node

    def walk_super(self, token, children):
        return self.walk_this(token, children, True)

    def walk_SuperSuffix(self, token, children):
        assert len(children) >= 1
        left = self.pop()
        right = None
        types = []
        arguments = None
        for c, type_, text in children.iteritems():
            if right is None:
                right = self.dispatch(c)
            elif type_ == tok.Arguments:
                arguments = self.dispatch(c)
            elif type_ == tok.TypeArguments:
                types.append(self.dispatch(c))
            else:
                self.unknown_token(c)
        return self.node(token, ast.TypedSuffix, left, right, types, arguments)

    def walk_switch(self, token, children):
        selector = None
        entries = []
        for c, type_, text in children.iteritems():
            if type_ == tok.SwitchEntry:
                entries.append(self.dispatch(c))
            elif selector is None:
                selector = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.Switch, selector, entries)

    def walk_SwitchEntry(self, token, children):
        test = None
        nodes = []
        for c, type_, text in children.iteritems():
            if type_ == tok.CASE:
                assert len(c.children) ==  1
                test = self.dispatch(c.children[0])
            elif type_ == tok.DEFAULT:
                pass
            else:
                nodes.append(self.dispatch(c))
        return self.node(token, ast.SwitchEntry, test, nodes)

    def walk_synchronized(self, token, children, super=False):
        expr = self.dispatch(children[0])
        nodes = self.dispatch(children[1])
        assert len(children) == 2
        return self.node(token, ast.Synchronized, expr, nodes)

    def walk_this(self, token, children, super=False):
        if super:
            name = ['super']
            cls = ast.Super
        else:
            name = ['this']
            cls = ast.This
        suffix = []
        arguments = None
        array = None
        for c, type_, text in children.iteritems():
            if type_ == tok.Arguments:
                arguments = self.dispatch(c)
            elif type_ == tok.IDENTIFIER:
                name.append(text)
            elif type_ == tok.TypedSuffix:
                suffix.append(c)
            elif type_ == tok.SuperSuffix:
                suffix.append(c)
            elif type_ == tok.ArrayAccess:
                array = self.dispatch(c)
            else:
                self.unknown_token(c)
        assert len(suffix) <= 1
        if len(name) == 1 and arguments is not None:
            return self.node(token, cls, arguments)
        if len(name) == 1:
            node = self.node(token, ast.Identifier, name[0], suffix)
        else:
            name = '.'.join(name)
            node = self.node(token, ast.QualifiedIdentifier, name, suffix)
        if arguments is not None:
            return self.node(token, ast.CallFunc, node, arguments)
        if array is not None:
            return self.node(token, ast.ArrayAccess, node, array)
        if len(suffix) == 0:
            return node
        c = suffix[0]
        type_, text = c.getType(), c.getText()
        if type_ == tok.CLASS:
            node.suffix = 'class'
            return node
        if type_ == tok.TypedSuffix:
            self.push(node)
            return self.dispatch(c)
        if super and type_ == tok.SuperSuffix:
            self.push(node)
            return self.dispatch(c)
        raise JavaAstError(token, "Invalid this expression %r, %r:%r" % (
            token, c, type_))

    def walk_throw(self, token, children):
        assert len(children) == 1
        expr = self.dispatch(children[0])
        return self.node(token, ast.Throw, expr)

    def walk_throws(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_try(self, token, children):
        nodes = []
        catches = []
        finally_ = None
        for c, type_, text in children.iteritems():
            if type_ == tok.Block:
                nodes = self.dispatch(c)
            elif type_ == tok.CATCH:
                catches.append(self.dispatch(c))
            elif type_ == tok.FINALLY:
                finally_ = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.Try, nodes, catches, finally_)

    def walk_Type(self, token, children):
        array = []
        node = []
        for c, type_, text in children.iteritems():
            if type_ == tok.ClassOrInterfaceType:
                node = self.dispatch(c)
            elif type_ == tok.PrimitiveType:
                node = self.dispatch(c)
            elif type_ == tok.LBRACKET:
                array.append('[]')
            else:
                self.unknown_token(c)
        return self.node(token, ast.Type, node, array)

    def walk_TypeArguments(self, token, children):
        flagged = None
        arguments = []
        for c, type_, text in children.iteritems():
            if type_ == tok.EXTENDS:
                flagged = text
            elif type_ == tok.SUPER:
                flagged = text
            elif type_ == tok.Type:
                t = self.dispatch(c)
                arguments.append((t, flagged))
                flagged = None
            else:
                self.unknown_token(c)
        return arguments

    def walk_TypedSuffix(self, token, children):
        assert len(children) == 3
        left = self.pop()
        right = self.dispatch(children[0])
        types = self.dispatch(children[1])
        arguments = self.dispatch(children[2])
        return self.node(token, ast.TypedSuffix, left, right, types, arguments)

    def walk_TypeList(self, token, children):
        return [self.dispatch(c) for c in children]

    def walk_TypeParameters(self, token, children):
        nodes = []
        for c in children:
            name = c.getText()
            extends = []
            if c.getChildCount() > 0:
                for t in c.getChildren():
                    extends.append(t.getText())
            nodes.append(self.node(c, ast.TypeParameter, name, extends))
        return nodes

    def walk_UnaryExpression(self, token, children):
        op = None
        node = None
        for c, type_, text in children.iteritems():
            if text in ['+', '-', '++', '--', '~', '!']:
                assert op is None
                op = text
            elif node is None:
                node = self.dispatch(c)
            elif type_ == tok.Selector:
                self.push(node)
                node = self.dispatch(c)
            else:
                self.unknown_token(c)
        return self.node(token, ast.UnaryExpr, node, op)

    def walk_VariableDeclarator(self, token, children):
        name = None
        array = []
        initializer = None
        for c, type_, text in children.iteritems():
            if name is None and type_ == tok.IDENTIFIER:
                name = text
            elif type_ == tok.LBRACKET:
                array.append("[]")
            else:
                if initializer is None:
                    initializer = self.dispatch(c)
                else:
                    self.unknown_token(c)
        return self.node(
            token, ast.VariableDeclarator, name, array, initializer,
        )

    def walk_VoidClass(self, token, children):
        array = []
        name = children[0].text
        for c in children[1:]:
            array.append('[]')
        node = self.node(token, ast.PrimitiveType, name)
        return self.node(token, ast.ClassSuffix, node, array)

    def walk_while(self, token, children):
        test = None
        node = None
        comment_list = []
        for c, type_, text in children.iteritems():
            if test is None and type_ == tok.ParExpression:
                test = self.dispatch(c)
            elif node is None:
                nodes = self.dispatch(c)
            else:
                self.unknown_token(c)
        comment_list.append(self.retrieveComment(token, trailing=False))
        comment_list.append(self.retrieveComment(token))
        node = self.node(token, ast.While, test, nodes)
        node.comments = comment_list
        return node


if __name__ == '__main__':
    import sys
    import os
    sys.path.append("antlr-3.3-complete.jar")
    sys.path.append("javaparser.jar")
    import JavaLexer
    import JavaParser
    import org

    lexer = JavaLexer(org.antlr.runtime.ANTLRFileStream(sys.argv[1]))
    tokens = org.antlr.runtime.CommonTokenStream(lexer)
    parser = JavaParser(tokens)
    cu = parser.compilationUnit()
    tree = cu.getTree()
    tree.getChildCount()

    if 0:
        def printChilds(indent, tree):
            #print tree, dir(tree), tree.__class__.__name__
            if tree.getChildCount() > 0:
                for c in tree.getChildren():
                    print "%s%s" % ('  ' * indent, c)
                    printChilds(indent + 1, c)
            #else:
            #    for c in tree:
            #        print "%s%s" % ('  ' * indent, c)
            #        printChilds(indent + 1, c)
        printChilds(0, tree)
        sys.exit(0)

    
    comments = []
    javadocs = []
    for i in range(tokens.size()):
        t = tokens.get(i)
        if t.getChannel() == t.HIDDEN_CHANNEL:
            if t.getText().startswith("/**"):
                javadocs.append(t)
            else:
                comments.append(t)

    #for c in comments:
    #    print c
    walker = JavaWalker()
    w = walker.walk(tree, tokens)
    print w
