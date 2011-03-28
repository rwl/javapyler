#!/bin/bash

java -jar antlr-3.3-complete.jar Java.g
javac -nowarn -cp antlr-3.3-complete.jar JavaLexer.java JavaParser.java 
jar cf javaparser.jar *.class
rm *.class

TOKENSPY=JavaTokens.py
grep -v "^'" Java.tokens >$TOKENSPY
cat <<EOM >>$TOKENSPY

strings = {
EOM
(
	grep "^'" Java.tokens |\
	sed "s/'=\([0-9]\+\)/':\1/g" |\
 	while read line ; do
		echo "    $line,"
	done
)>>$TOKENSPY
cat <<EOM >>$TOKENSPY
}

rev_strings = {}
for k, v in strings.iteritems():
    rev_strings[v] = k
EOM
