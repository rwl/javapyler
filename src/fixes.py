
if repr(True) != 'True':
    # Workaround for jython not having proper True/False
    class Boolean(int):

        def __init__(self, v):
            self.v = v

        def __int__(self):
            return self.v

        def __repr__(self):
            if self.v:
                return 'True'
            else:
                return 'False'

        def __str__(self):
            return self.__repr__()
    False = Boolean(0)
    True = Boolean(1)
