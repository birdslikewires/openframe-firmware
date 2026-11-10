#!/bin/sh
# Run a command on an OpenFrame over SSH: OF_HOST=<address> tools/of.sh '<command>'
# e.g. OF_HOST='fe80::e:8eff:fe12:3456%en0' (the IPv6 link-local address from the LAN MAC).
# Login is the image default (of / joggler, see openframe-linux 00-of-prep.sh). It needs an
# SSH_ASKPASS helper that prints the password, e.g. a two-line script ending `echo joggler`.
: "${OF_HOST:?set OF_HOST to the unit's address}"
SSH_ASKPASS="${SSH_ASKPASS:-$HOME/.of-askpass}" SSH_ASKPASS_REQUIRE=force exec ssh -o ConnectTimeout=8 -o LogLevel=ERROR "of@$OF_HOST" "$@"
