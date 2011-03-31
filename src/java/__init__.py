
def ARGERROR(min, max):
    return TypeError('Expected %d to %d arguments' % (min, max))

def _get_variable(glob, loc, name):
    names = name.split('.')
    name = names.pop(0)
    if name in loc:
        base = loc
    else:
        base = glob
    v = base[name]
    while names:
        base = v
        name = names.pop(0)
        v = getattr(v, name)
    return base, name, v

def POSTINC(glob, loc, name):
    base, name, v = _get_variable(glob, loc, name)
    if isinstance(base, dict):
        base[name] += 1
    else:
        setattr(base, name, v + 1)
    return v

def POSTDEC(glob, loc, name):
    base, name, v = _get_variable(glob, loc, name)
    if isinstance(base, dict):
        base[name] -= 1
    else:
        setattr(base, name, v - 1)
    return v

def PREINC(glob, loc, name):
    base, name, v = _get_variable(glob, loc, name)
    v = v + 1
    if isinstance(base, dict):
        base[name] = v
    else:
        setattr(base, name, v)
    return v

def PREDEC(glob, loc, name):
    base, name, v = _get_variable(glob, loc, name)
    v = v - 1
    if isinstance(base, dict):
        base[name] = v
    else:
        setattr(base, name, v)
    return v

def URSHIFT(v, bits):
    return (v >> bits) & 0x7FFFFFFF
