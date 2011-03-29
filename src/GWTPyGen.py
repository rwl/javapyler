
from PyGen import PyGen


class GWTPyGen(PyGen):

    def addCommentCode(self, comment):
        if isinstance(comment, list):
            for c in comment:
                self.addCommentCode(c)
            return
        scomment = comment.strip()
        if scomment.startswith('-{') and scomment.endswith('}-'):
            scomment = scomment[2:-2]
            self.addCode('JS("""%s""")' % scomment)
        else:
            super(GWTPyGen, self).addCommentCode(comment)
