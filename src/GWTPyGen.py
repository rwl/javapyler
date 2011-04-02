
from PyGen import PyGen
import re


class GWTPyGen(PyGen):
    locals = None

    def addCommentCode(self, comment):
        if comment is None:
            return
        if isinstance(comment, list):
            for c in comment:
                self.addCommentCode(c)
            return
        scomment = comment.strip()
        if scomment.startswith('-{') and scomment.endswith('}-'):
            scomment = scomment[2:-2]
            if self.locals is not None:
                for v in self.locals.keys():
                    patt = '\\b%s\\b' % v
                    repl = '@{{%s}}' % v
                    scomment = re.sub(patt, repl, scomment)
            self.addCode('JS("""%s""")' % scomment)
        else:
            super(GWTPyGen, self).addCommentCode(comment)

    def astFunction(self, node):
        if hasattr(node, 'locals'):
            locs = self.locals
            self.locals = node.locals
        PyGen.astFunction(self, node)
        if hasattr(node, 'locals'):
            self.locals = locs
