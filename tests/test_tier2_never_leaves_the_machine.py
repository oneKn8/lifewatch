"""The privacy boundary that Tier 2 exists inside.

Tier 2 reads raw window titles: documents, URLs, message previews, everything the
operator looks at. Spec section 7 puts it on the local side of the data boundary
for that reason, and section 12 states plainly that titles do not leave the
machine.

That promise is worth exactly as much as the check that enforces it. The first
implementation enforced it with ``host.startswith("127.")``, which is a string
test wearing an address test's clothes:

    _is_on_machine("127.evil-exfil.com")  ->  True

A DNS name that begins with "127." resolves wherever its owner points it. These
tests pin the parsed-address behaviour so the string version cannot come back.
"""

import pytest

from lifewatch.classify.tier2 import _is_on_machine

REAL_LOOPBACK = [
    "localhost",
    "127.0.0.1",
    "127.0.0.53",
    "127.1.2.3",
    "::1",
    "[::1]",
    "0:0:0:0:0:0:0:1",
]

NOT_THIS_MACHINE = [
    "127.evil-exfil.com",
    "127.attacker.example",
    "127.0.0.1.evil.example",
    "1270.0.0.1",
    "example.com",
    "192.168.1.10",
    "10.0.0.1",
    "0.0.0.0",
    "8.8.8.8",
    "my-other-laptop.local",
    "",
    "   ",
]


@pytest.mark.parametrize("host", REAL_LOOPBACK)
def test_genuine_loopback_addresses_are_accepted(host):
    assert _is_on_machine(host) is True


@pytest.mark.parametrize("host", NOT_THIS_MACHINE)
def test_anything_that_is_not_provably_this_machine_is_rejected(host):
    assert _is_on_machine(host) is False, (
        f"{host!r} was accepted as local; window titles would be sent to it"
    )


def test_a_dns_name_beginning_with_127_is_not_loopback():
    """The exact regression. A name is not an address."""
    assert _is_on_machine("127.evil-exfil.com") is False


def test_the_gate_fails_closed_on_junk():
    for junk in ["::::", "not an address", "127.0.0", "999.999.999.999"]:
        assert _is_on_machine(junk) is False


def test_the_check_is_not_a_prefix_test():
    """Guards against reintroducing startswith on a numeric prefix.

    If someone reverts to prefix matching, the host below passes and this fails.
    """
    assert _is_on_machine("127.0.0.1") is True
    assert _is_on_machine("127.0.0.1.example.com") is False
