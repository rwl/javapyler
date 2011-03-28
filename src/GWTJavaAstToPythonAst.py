from JavaAstToPythonAst import JavaAstToPythonAst


class GWTJavaAstToPythonAst(JavaAstToPythonAst):

    def mapImport(self, name):
        gwt = "com.google.gwt."
        if name.startswith(gwt):
            name = "pyjamas.%s" % name[len(gwt):]
            name = name.replace('.user.client.', '.')
            name = name.replace('.client.', '.')
            name = name.replace('pyjamas.dom.', 'pyjamas.DOM.')
            return name
        return super(GWTJavaAstToPythonAst, self).mapImport(name)
