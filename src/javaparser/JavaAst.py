
class Node(object):
    lineno = None
    charpos = None
    comments = None

    def __init__(self, attrs):
        attrs = dict(attrs)
        self = attrs.pop('self')
        self.keys = attrs.keys()
        self.keys.sort()
        for k, v in attrs.iteritems():
            setattr(self, k, v)

    def __repr__(self):
        attrs = []
        for k in self.keys:
            v = getattr(self, k)
            if isinstance(v, Node):
                attrs.append("%s=%r" % (k, v))
            elif v is None:
                pass
            elif hasattr(v, '__getitem__'):
                try:
                    #if not isinstance(v[0], Node):
                    #    attrs.append("%s=%r" % (k, v))
                    attrs.append("%s=%r" % (k, v))
                except IndexError:
                    pass
            else:
                attrs.append("%s=%r" % (k, v))
        if self.comments:
            attrs.append("comments=%r" % (self.comments, ))
        return "%s(%s)" % (
            self.__class__.__name__,
            ", ".join(attrs),
        )

    def __cmp__(self, other):
        if self.__class__ is not other.__class__:
            return -1
        for k in self.keys:
            c = cmp(getattr(self, k), getattr(other, k))
            if c:
                return c
        return 0


class EmptyNode(Node):

    def __init__(self):
        Node.__init__(self, locals())


class LeftOpRight(Node):

    def __init__(self, left, op, right):
        Node.__init__(self, locals())

    def __repr__(self):
        attrs = [
            "left=%r" % self.left,
            "op=%r" % self.op,
            "right=%r" % self.right,
        ]
        return "%s(%s)" % (
            self.__class__.__name__,
            ", ".join(attrs),
        )


class TypeNode(Node):
    pass





class AdditiveExpr(LeftOpRight):
    pass


class AndExpr(LeftOpRight):
    pass


class Annotation(Node):

    def __init__(self, name, arguments):
        Node.__init__(self, locals())


class AnnotationMethod(TypeNode):

    def __init__(self, name, type, elem, modifiers):
        Node.__init__(self, locals())


class AnnotationType(TypeNode):

    def __init__(self, name, nodes, modifiers):
        Node.__init__(self, locals())


class ArrayAccess(Node):

    def __init__(self, name, array):
        Node.__init__(self, locals())


class ArrayCreator(Node):

    def __init__(self, name, array, initializer):
        Node.__init__(self, locals())


class Assert(Node):

    def __init__(self, test, value):
        Node.__init__(self, locals())


class AssignExpr(LeftOpRight):
    pass


class BitAndExpr(LeftOpRight):
    pass


class BitOrExpr(LeftOpRight):
    pass


class BitXorExpr(LeftOpRight):
    pass


class Break(Node):

    def __init__(self, label):
        Node.__init__(self, locals())


class CallFunc(Node):

    def __init__(self, node, arguments):
        Node.__init__(self, locals())


class CastExpr(Node):

    def __init__(self, node, type):
        Node.__init__(self, locals())


class Class(Node):

    def __init__(self, name, doc, members, typeParameters, extends, 
                 implements, modifiers):
        Node.__init__(self, locals())


class ClassBlock(Node):

    def __init__(self, nodes, modifiers):
        Node.__init__(self, locals())


class ClassCreator(Node):

    def __init__(self, type, typeArguments, arguments, nodes):
        Node.__init__(self, locals())


class ClassOrInterfaceType(TypeNode):

    def __init__(self, types):
        Node.__init__(self, locals())


class Comment(Node):

    def __init__(self, text):
        Node.__init__(self, locals())


class CompilationUnit(Node):

    def __init__(self, annotations, package, imports, types):
        Node.__init__(self, locals())


class ConditionalExpr(Node):

    def __init__(self, test, then, else_):
        Node.__init__(self, locals())


class Continue(Node):

    def __init__(self, label):
        Node.__init__(self, locals())


class DoWhile(Node):

    def __init__(self, test, nodes):
        Node.__init__(self, locals())


class ForEach(Node):

    def __init__(self, var, test, nodes):
        Node.__init__(self, locals())


class Enum(Node):

    def __init__(self, name, doc, members, constants, implements, modifiers):
        Node.__init__(self, locals())


class EnumConstant(Node):

    def __init__(self, name, annotations, arguments, nodes):
        Node.__init__(self, locals())


class EqualityExpr(LeftOpRight):
    pass


class FieldDeclaration(Node):

    def __init__(self, variables, type, modifiers):
        Node.__init__(self, locals())


class For(Node):

    def __init__(self, init, expr, update, nodes):
        Node.__init__(self, locals())


class Identifier(Node):

    def __init__(self, name, suffix):
        Node.__init__(self, locals())


class If(Node):

    def __init__(self, test, then, else_):
        Node.__init__(self, locals())


class Import(Node):

    def __init__(self, name, wildcard, modifiers):
        Node.__init__(self, locals())


class InstanceOfExpr(Node):

    def __init__(self, node, type):
        Node.__init__(self, locals())


class Interface(Node):

    def __init__(self, name, doc, members, typeParameters, extends, modifiers):
        Node.__init__(self, locals())


class JavaDoc(Node):

    def __init__(self, text):
        Node.__init__(self, locals())


class Label(Node):

    def __init__(self, name, node):
        Node.__init__(self, locals())


class LineComment(Node):

    def __init__(self, text):
        Node.__init__(self, locals())


class Literal(Node):

    def __init__(self, value, type):
        Node.__init__(self, locals())

    def __repr__(self):
        attrs = [
            "value=%r" % self.value,
            "type=%r" % self.type,
        ]
        return "%s(%s)" % (
            self.__class__.__name__,
            ", ".join(attrs),
        )


class LocalVariableDeclaration(Node):

    def __init__(self, variables, type, modifiers):
        Node.__init__(self, locals())


class Method(Node):

    def __init__(self, name, doc, body, parameters, typeParameters, 
                 modifiers, throws):
        Node.__init__(self, locals())


class MultiplicativeExpr(LeftOpRight):
    pass


class OrExpr(LeftOpRight):
    pass


class Package(Node):

    def __init__(self, name):
        Node.__init__(self, locals())


class ParameterDecl(Node):

    def __init__(self, name, type, type_modifier, modifiers):
        Node.__init__(self, locals())


class ParExpr(Node):

    def __init__(self, node):
        Node.__init__(self, locals())


class PrimitiveType(TypeNode):

    def __init__(self, name):
        Node.__init__(self, locals())


class PostOperator(Node):

    def __init__(self, node, op):
        Node.__init__(self, locals())


class QualifiedIdentifier(Identifier):
    pass


class RelationalExpr(LeftOpRight):
    pass


class Return(Node):

    def __init__(self, expr):
        Node.__init__(self, locals())


class Selector(Node):

    def __init__(self, left, right, types):
        Node.__init__(self, locals())


class ShiftExpr(LeftOpRight):
    pass


class Super(CallFunc):

    def __init__(self, arguments):
        node = Identifier("super", None)
        CallFunc.__init__(self, node, arguments)


class Switch(Node):

    def __init__(self, selector, entries):
        Node.__init__(self, locals())


class SwitchEntry(Node):

    def __init__(self, test, nodes):
        Node.__init__(self, locals())


class This(CallFunc):

    def __init__(self, arguments):
        node = Identifier('this', None)
        CallFunc.__init__(self, node, arguments)


class Throw(Node):

    def __init__(self, node):
        Node.__init__(self, locals())


class Try(Node):

    def __init__(self, nodes, catches, finally_):
        Node.__init__(self, locals())


class Type(TypeNode):

    def __init__(self, type, array, final=False):
        Node.__init__(self, locals())


class TypedSuffix(Node):

    def __init__(self, left, right, types, arguments):
        Node.__init__(self, locals())


class TypeParameter(Node):

    def __init__(self, name, extends):
        Node.__init__(self, locals())

class UnaryExpr(Node):

    def __init__(self, node, op):
        Node.__init__(self, locals())


class UnaryExpr(Node):

    def __init__(self, node, op):
        Node.__init__(self, locals())


class VariableDeclarator(Node):

    def __init__(self, name, array, initializer):
        Node.__init__(self, locals())


class While(Node):

    def __init__(self, test, nodes):
        Node.__init__(self, locals())


