#!/usr/bin/bash

echo -e "Enter a character: \c"
read value
#environment variable to set the language
LANG=C
case $value in
    [a-z] )
        echo "lower case" ;;
    [A-Z] )
        echo "upper case" ;;
    [0-9] )
        echo "digit" ;;
    ? )
        echo "special" ;;
    * )
        echo "unknown input" ;;
esac        
    