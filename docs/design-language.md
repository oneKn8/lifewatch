# Instrument — the lifewatch design language

The visual language of **lifewatch**, an accountability engine that notices in
real time when a declared commitment is not being kept.

It is not a dashboard system. Everything here is shaped by one situation: a wall
panel read from across a dark room, on a 2011 720p television with a tired
backlight and poor viewing angles, by someone who did not choose to look at it.

## The rules that matter more than the palette

1. **Red is reserved.** `--lost` means time that is gone. It is never a border,
   never an accent, never emphasis. If red appears, hours were lost.
2. **Radius is zero.** A rounded corner reads as a card, a card reads as an app,
   and this is a panel.
3. **No opacity on text, ever.** Faded text vanishes at an angle on a tired LCD.
   Use `--dim` as a real colour instead.
4. **Nothing below 700 weight on the wall.** Thin strokes disappear at distance.
5. **Tabular numerals everywhere.** These numbers change while being watched;
   proportional digits make them jitter.
6. **The wall accepts no input.** No buttons, no links, no focus states. The
   phone is the control surface. This is enforced by a test in the repo.
7. **Motion is earned or absent.** The grid moves because hours really are
   passing. Nothing else animates, and nothing blinks: a blinking screen is
   ignored within a week, and this has to still work in week twelve.

## Three surfaces

- **Wall** — three numbers and the term grid. Glanceable in one second.
- **Phone** — the control surface. Start, complete, move, pass, sick.
- **Setup** — run once, standing in the places being named.
