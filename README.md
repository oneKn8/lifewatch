# lifewatch

An accountability engine. It notices, in real time, when a commitment you declared is not
being kept, and escalates until you either keep it or renegotiate it.

It is not a time tracker and not a productivity dashboard. Those are passive: they wait to
be opened, and the person who most needs one stops opening it by the third week. lifewatch
runs whether or not you are operating it, and it interrupts.

Everything is local. There is no account, no server, no telemetry, and nothing is uploaded.

## The problem it addresses

Some people do not act on commitments that only they can see. The commitment is real and
the ability is present, and the work still does not happen, because nothing in the
environment notices the gap while it is opening. By the time the consequence is visible,
the term is over.

That is a supervision problem, not a scheduling problem. Existing tools do not solve it:

| Approach | Why it fails |
| --- | --- |
| Self-report | You round up in your own favour |
| Automatic activity tracking | Blind to work done on paper; scores your best hours as idleness, and scores three focused hours of distraction as engagement |
| Passive dashboards | Must be opened, and eventually are not |
| Institutional monitoring | Closes the gap by removing consent. Different product, different ethics |

## How it works

Three ideas carry the design.

**The discrepancy is the product.** You declare a block. Sensors observe what actually
happened. Neither number alone is trustworthy, but the gap between them is: it is what
self-report cannot fake and what automatic capture cannot see on its own.

**Absence is the loudest signal, not the quietest.** Most trackers treat "no activity" as
neutral, presuming you are elsewhere doing something legitimate. During a block you
declared, at a place you said you would be, with no input and no desk presence, absence is
the specific event this exists to catch. A dead block does not log a zero. It escalates.

**Renegotiation is the only exit.** There is no dismiss button. A block can be started,
completed, or *moved*, and moving it means naming where the hours land. They reappear as
debt rather than evaporating. This is what makes an aggressive escalation ladder safe: the
system is only ever harsh toward silence, never toward renegotiation.

## Architecture

Seven units, each with one job, communicating through an append-only log.

```
sensors ──► store ──► classifier ──► watcher ──► effectors
              ▲            ▲            ▲
              └── contract ┴────────────┘
                                        │
                                        └──► views
```

| Unit | Job |
| --- | --- |
| `sensors` | Answer one narrow factual question about the present moment |
| `store` | Append-only observation log. No update path, no delete path |
| `classifier` | Turn observations into aligned / ambient / drift / absent / accounted |
| `contract` | The declared commitments, budgets, exceptions |
| `watcher` | Compare contract to reality; decide whether and how hard to intervene |
| `effectors` | Deliver an intervention through some channel |
| `views` | A glanceable wall display and an interactive phone view |

**The engine contains no domain knowledge.** Everything about a particular person — their
commitments, their places, how hard they asked to be pushed, why they are doing it — is
configuration collected by the setup wizard at runtime. That is deliberate: a value that
ships is a value that gets committed by accident.

## Classification

Three tiers, cheapest first.

**Tier 1, mechanical.** Structural facts needing no interpretation. The highest-value rule:
media playing while a *different* window has focus is ambient listening, not watching. That
single rule resolves most of the "music while studying versus entertainment" ambiguity with
no title classification at all.

**Tier 2, judgment.** When an application is focused and its title is ambiguous, a language
model judges the title against the active commitment. A lecture and an entertainment video
are trivially separable by title, which is what a model is good at and what a hardcoded
blocklist is bad at.

**Tier 3, ask and learn.** Genuine ambiguity produces one question, answered with one tap,
and the answer is remembered.

### Where inference runs, and why

The split follows the data boundary, not preference.

**Tier 2 runs locally, always.** Its input is raw window titles: your documents, your URLs,
everything you look at. That is the most sensitive data in the system and it does not leave
the machine. A 3B-class model is plenty for this classification, and small enough not to
heat the laptop you are trying to work on.

**Watcher judgment may use a cloud model, opt-in.** It needs real contextual reasoning and
runs a few times a day. Its input is *derived state only* — dead block counts, idle
minutes, passes remaining — and never a title, URL, or raw observation.

The mandatory path is entirely local. Clone it and it works, with no key and no account.

## Exceptions

A system with no legitimate exit gets uninstalled the second time it is wrong.

- **Move.** The routine exit. Hours relocate as debt. No penalty, no comment.
- **Pass.** A finite, visible, no-questions-asked skip. Unlimited means there is no system;
  zero means you break the system instead of using it.
- **Sick mode.** Everything silent for 24 hours. You will get sick, and a system that
  punishes influenza is one you will kill.
- **Discretion.** The watcher reasons rather than enforces, and it may only ever choose a
  *softer* response than the ladder specifies, never a harder one.

## The constraint that keeps this from being cruelty

> No escalation may be delivered without a resolvable next action.

Every interruption carries something concrete to do right now. If the watcher cannot
resolve one, it stays silent. This is a precondition of the `Intervention` type itself, not
a caller-side check, so no code path in the system can deliver a bare reproach.

Shame produces avoidance, and avoidance is the failure mode being treated. A system that
only reports failure makes not-starting more attractive, not less. Loss and recovery appear
in the same frame or not at all.

## Privacy

The subject is the operator. That permits sensing which would be unacceptable otherwise,
and obliges strict limits.

- The optional presence sensor points at the **work seat, not the bed**. It answers "is the
  seat occupied," which is the same information with far less exposure and no recording of
  sleep.
- **No frame is ever written to disk and none leaves the machine.** The pipeline derives one
  boolean and discards the image. Enforced by a test that fails the build on any image
  write.
- Location is inferred from network name only. No GPS, no coordinates, no third-party
  service.
- Window titles are stored locally and never transmitted unless you explicitly enable the
  cloud classifier backend, in which case only the ambiguous title is sent.

## Status

Stage 1, in development. Target: usable 2026-08-24.

| | |
| --- | --- |
| **Stage 1** | Engine, store, wizard, `school` pack, window/idle/network sensors, classifier tiers 1-2, contract with move/pass/sick, watcher rungs 1-2, grid, wall view, phone view |
| **Stage 2** | Presence sensor, grade model, light and phone-call rungs, learned classification |
| **Stage 3** | A second domain pack. This is what settles the plugin interface |

Design documents live in `docs/superpowers/`.

## Domain packs

The engine is domain-agnostic; `school` is simply the first pack. A pack supplies data and
optional plugins: commitment fields, any specialist model (the school pack computes what
score is still needed on remaining work to reach a target grade), and mode behaviour.

No plugin API has been designed yet, on purpose. Guessing extension points from a single
example gets them wrong. The seam will be cut where the config boundary already falls, once
a second real pack exists to define it.

## Requirements

- Linux with **X11** (Wayland needs a replacement `window` sensor; the `Sensor` protocol
  exists so that is a contained change)
- Python 3.10+
- `xprop` and `iwgetid`, both standard

## Development

```bash
make venv    # create .venv, install editable with dev extras
make test    # run the suite
make run     # start the sensor runner and web server
```

Use `make test` rather than bare `pytest`. If ROS 2 is sourced globally on your machine, it
forces `PYTHONPATH` to its own site-packages and leaks its pytest plugins into any
virtualenv, failing collection on an unrelated import. Every Make target scrubs the
environment first.

## Licence

Apache-2.0.
