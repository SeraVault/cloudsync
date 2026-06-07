#!/bin/sh
# Open a Google Workspace stub file (.gdoc, .gsheet, etc.) in the browser.
# The stub is a JSON file with a "url" key written by CloudSync.
url=$(python3 -c "import sys, json; print(json.load(open(sys.argv[1]))['url'])" "$1" 2>/dev/null)
if [ -z "$url" ]; then
    echo "Could not read URL from $1" >&2
    exit 1
fi
xdg-open "$url"
