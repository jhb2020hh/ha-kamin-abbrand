#!/bin/sh
# exec, damit SIGTERM direkt beim Python-Prozess ankommt.
exec python3 -m anbindung.main
