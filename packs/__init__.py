"""Domain packs.

The engine in ``lifewatch/`` knows about blocks, time, and escalation, and
nothing about what any of it is for. A pack supplies the domain: the fields a
commitment carries, and the small amount of arithmetic that only means anything
inside that domain.

Exactly one pack ships: ``school``. No plugin API is designed here, because an
extension interface guessed from a single example gets guessed wrong and arrives
late. The seam is the config boundary that already exists, and it will be carved
properly when a second real pack exists. See the design spec section 11.1.
"""
