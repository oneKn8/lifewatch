"""Turning observations into a verdict about an hour.

Three tiers, cheapest first. Tier 1 is mechanical, Tier 2 asks a local model to
judge an ambiguous window title, Tier 3 asks the person. Each tier that cannot
decide returns nothing and the next one runs; the class recorded on every
interval carries the tier that produced it, so any verdict can be traced back to
the rule or the judgment that reached it.

Only Tier 1 exists so far. The ``Classifier`` that dispatches between the tiers
arrives with Tier 2 and Tier 3.
"""
