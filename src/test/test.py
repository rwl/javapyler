
from javapyler import main, parser, JavaPyler


args = ['--config', 'javapyler.cfg', '--output', 'out', 'java/Assign1.java']
#args = ['--javalib', '__pyjamas__', '--java-base', 'javapyler',
#'--output', 'out', 'java', 'java/Package1']


if __name__ == '__main__':
    options, args = parser.parse_args(args)
    main(JavaPyler, options, args)
