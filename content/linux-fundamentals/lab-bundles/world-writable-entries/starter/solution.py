"""World-writable entries / Entrées modifiables par tout le monde.

EN: Read `ls -l` output on standard input and print the name of every world-writable entry,
    one per line, in input order.
    - A symbolic link is never reported (its mode bits are not enforced).
    - A directory with the sticky bit AND world-write (drwxrwxrwt) is reported.
    - A sticky entry without world-write (drwxr-xr-t) is not reported.
    - Ignore the "total" line. Names may contain spaces.

FR : Lisez la sortie de `ls -l` sur l'entrée standard et affichez le nom de chaque entrée
    modifiable par tout le monde, un par ligne, dans l'ordre de l'entrée.
    - Un lien symbolique n'est jamais signalé (ses bits de mode ne sont pas appliqués).
    - Un dossier avec le bit sticky ET l'écriture pour tous (drwxrwxrwt) est signalé.
    - Une entrée sticky sans écriture pour tous (drwxr-xr-t) n'est pas signalée.
    - Ignorez la ligne « total ». Les noms peuvent contenir des espaces.
"""

import sys


def main() -> None:
    for line in sys.stdin:
        line = line.rstrip("\n")
        # TODO: skip the "total" line and blank lines.
        # TODO: split the line into fields; the mode (for example "-rw-rw-rw-") comes first
        #       and the name is everything after the 8th field.
        # TODO: decide from the mode whether the entry is world-writable.
        # TODO: print the name when it must be reported.
        pass


if __name__ == "__main__":
    main()
