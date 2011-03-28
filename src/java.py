
def ARGERROR(min, max):
    return TypeError('Expected %d to %d arguments' % (min, max))

def POSTINC(glob, loc, name):
    if name in loc:
        v = loc[name]
        loc[name] += 1
    elif name in glob:
        v = glob[name]
        glob[name] += 1
    return v

def POSTDEC(glob, loc, name):
    if name in loc:
        v = loc[name]
        loc[name] -= 1
    elif name in glob:
        v = glob[name]
        glob[name] -= 1
    return v

def PREINC(glob, loc, name):
    if name in loc:
        loc[name] += 1
        v = loc[name]
    elif name in glob:
        glob[name] += 1
        v = glob[name]
    return v

def PREDEC(glob, loc, name):
    if name in loc:
        loc[name] -= 1
        v = loc[name]
    elif name in glob:
        glob[name] -= 1
        v = glob[name]
    return v

def URSHIFT(v, bits):
    return (v >> bits) & 0x7FFFFFFF
