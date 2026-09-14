#!/bin/zsh
set -eu
cd '/Users/milesdipaola/Projects/savvy-selects-preview'
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
exec '/Users/milesdipaola/savvy-selects/.venv/bin/python' -m savvy.library.launch --workspace '/Users/milesdipaola/SavvyPreviewLibrary' --port 8421
