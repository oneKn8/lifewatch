# lifewatch — design

Date: 2026-08-15
Status: approved, pre-implementation

## 1. Problem

Some people do not act on commitments that only they can see. The commitment is real,
the ability is present, and the work still does not happen, because nothing in the
environment notices the gap while it is opening. By the time the consequence becomes
visible the term is over.

Existing tools do not close this. Time trackers are passive: they wait to be opened, and
the person who most needs one stops opening it. Automatic activity trackers watch the
wrong surface, scoring paper work as idleness and scoring three focused hours of
distraction as engagement. Institutional monitoring closes the gap by removing consent,
which is a different product with different ethics.

lifewatch is an **accountability engine**. It is self-imposed, locally run, and its job
is to notice in real time when a declared commitment is not being kept and to escalate
until the person either does the thing or explicitly renegotiates it.

The first user is a student whose stated failure mode is: "if someone is not watching me,
I will not study." That is a supervision problem, not a scheduling problem.

## 2. Non-goals

- **Not a productivity dashboard.** Charts of last month's hours change nothing.
  Everything the system displays is about the hour currently being spent.
- **Not institutional monitoring.** No remote administrator, no reporting to a third
  party, no proctoring. The subject is the operator.
- **Not a general life-logger.** It records only what is needed to judge whether a
  declared commitment is being kept.
- **Not a punishment machine.** See §12.
- **Not a plugin framework at v1.** See §11.

## 3. Core mechanism

Three ideas carry the design.

### 3.1 The discrepancy is the product

Self-report alone fails: people round up in their own favour. Automatic capture alone
fails: it cannot see work done away from the machine, and it cannot tell a lecture from
entertainment on the same domain.

The signal neither half produces alone is the **gap between them**. The user declares a
block. Sensors observe what actually happened. The difference between claimed minutes and
observed-aligned minutes is the honest number, and it is the number the system surfaces.

    integrity = aligned_minutes / claimed_minutes

A day of 6 claimed hours at 0.45 integrity is a materially different day from 3 claimed
hours at 0.95, and only this design can tell them apart.

### 3.2 Absence is the loudest signal, not the quietest

Conventional trackers treat "no activity" as neutral, because the user is presumed to be
elsewhere doing something legitimate. For this failure mode the inference is inverted:
during a block the user declared, at a place the user said they would be, with no input
and no desk presence, absence is the specific event the system exists to catch.

A dead block does not log a zero. It escalates.

### 3.3 Renegotiation is the only exit

There is no dismiss action. A block can be started, completed, or **moved**, and moving
it requires naming where the hours land. The unit of promise is the week's budget, not
the individual block, so relocated hours reappear as debt rather than evaporating.

This is what makes "go hard on me" safe to implement: the system is only ever harsh
toward silence, never toward renegotiation.

## 4. Architecture

Seven units. Each has one purpose, a defined interface, and can be tested alone. None of
them contain domain knowledge; see §11.

    sensors ──► store ──► classifier ──► watcher ──► effectors
                  ▲            ▲            ▲
                  └── contract ┴────────────┘
                                            │
                                            └──► views

| Unit | Purpose | Depends on |
| --- | --- | --- |
| **sensors** | Answer one narrow factual question about the present moment | OS |
| **store** | Append-only observation log plus derived state | SQLite |
| **classifier** | Turn observations into aligned / ambient / drift / absent / accounted | store, contract |
| **contract** | The declared commitments, budgets, exceptions, ladder | config |
| **watcher** | Compare contract to reality; decide whether and how hard to intervene | store, classifier, contract |
| **effectors** | Deliver an intervention through some channel | — |
| **views** | Wall display and phone interface | store, contract |

### 4.1 Interfaces

Sketches only; the point is the shape of the boundary.

```
Sensor:
    name        -> str
    available() -> bool          # can this run on this machine at all
    poll()      -> [Observation] # never blocks longer than the poll interval

Observation:
    ts, sensor, kind, value, meta

Effector:
    name        -> str
    available() -> bool
    deliver(Intervention) -> Delivery   # records whether it actually went out

Intervention:
    rung, block_id, message, next_action, requires_response
```

A sensor that reports `available() == False` is skipped without breaking anything above
it. This is what lets a Wayland or macOS contributor supply their own `window` sensor and
leave the rest of the system untouched.

## 5. Environment facts (verified 2026-08-15 on the target machine)

Checked directly rather than assumed, because the whole design rests on them.

| Fact | Value | Consequence |
| --- | --- | --- |
| Display server | **X11** (Pop!_OS 22.04, `pop:GNOME`) | Window titles and idle time are readable without root. Wayland would have blocked the primary sensor. |
| Active window | `xprop` returns `_NET_WM_NAME` live | `window` sensor is viable with tools already installed |
| Idle time | XScreenSaver extension returns idle ms | `idle` sensor needs no new dependency |
| Tools present | `xprop`, `xdotool` | `wmctrl` absent, not needed |
| Network | SSID readable via `iwgetid -r` | `network` sensor needs no permissions, no GPS, no battery cost |
| Cameras | `/dev/video0`, `/dev/video1` | `presence` sensor viable |
| Displays | `eDP-1` 1920x1200 active; **`HDMI-1` connected-ready, currently free** | Wall display needs one cable, no extra hardware |

**Correction on record:** an earlier session note treated `ATT-WIFI-7600` as the home
network. It is not; it was observed while the user was away from home. No SSID is written
into source or config defaults. Places are learned at runtime (§10).

## 6. Sensors

| Sensor | Question it answers | Method | Stage |
| --- | --- | --- | --- |
| `window` | What application and document has focus | `_NET_ACTIVE_WINDOW` → `_NET_WM_NAME`, `WM_CLASS` | 1 |
| `idle` | How long since any keyboard or mouse input | XScreenSaver `XScreenSaverQueryInfo` | 1 |
| `network` | Which known place is this | SSID matched against learned places | 1 |
| `presence` | Is the work seat occupied | Camera frame → person-in-region boolean (§12) | 2 |

Poll interval 15s for `window` and `idle`, 60s for `network`, 60s for `presence`.
Observations are written only on change plus a heartbeat, so the log stays small.

Sensors are strictly factual. `window` reports a title; it never decides whether that
title is good. All judgment lives in the classifier, so that judgment can be tested,
configured, and corrected without touching capture.

## 7. Classifier

Produces one of six classes per interval: `aligned`, `ambient`, `drift`, `absent`,
`accounted`, `unknown`.

Three tiers, cheapest first.

### Tier 1 — mechanical

Structural facts that need no interpretation. The most valuable one:

**Focused versus background media.** Media playing while a different window has focus is
`ambient` — the user is listening while working. The same application focused for a
sustained period is a candidate for `drift`. This single rule resolves most of the
"YouTube for study music versus YouTube for entertainment" ambiguity with no
classification at all.

Also mechanical: idle beyond threshold plus no desk presence during a block → `absent`.
Calendar-declared class or work time → `accounted`.

### Tier 2 — judgment

When an application is focused and its title is ambiguous, a language model judges the
title against the active commitment. A lecture series and an entertainment video are
trivially distinguishable by title; this is what a model is good at and what a hardcoded
blocklist is bad at.

Backend is pluggable. Default is a **local** model (Ollama), for three reasons: it is
free, it works offline, and it keeps window titles on the machine. A cloud backend is
available and **off by default**; enabling it sends ambiguous titles off-machine and the
wizard says so in those words.

### Tier 3 — ask, then learn

Genuine ambiguity produces one question, answered with one tap: *"`<title>` has been
focused 25 minutes during a 4349 block. Aligned or drift?"* The answer is stored as a
rule keyed on the source (channel, domain, or application), so the same question is never
asked twice. The ruleset is user data, not code.

**Asking ships in Stage 1**, because it is also Tier 2's fallback when no model backend is
available (§17.1). **Learning** — persisting the answer as a rule that pre-empts the next
question — ships in Stage 2. Until then an answer classifies only its own interval.

Every classification records which tier produced it, so the user can see why a verdict
was reached and correct it.

## 8. Contract

The declared commitments. Pure data.

```
Commitment:  id, label, weekly_target_minutes, pack_fields{}
Block:       id, commitment_id, planned_start, planned_end,
             actual_start, actual_end, state, moved_to, moved_from
             state ∈ planned | running | completed | moved | missed | excused
Budget:      week_start, target_minutes, banked_minutes, debt_minutes
Pass:        granted_per_week, remaining, used_at[]
```

### 8.1 Exceptions

A system with no legitimate exit is uninstalled the second time it is wrong. Four
mechanisms, in increasing weight:

1. **Move.** The default and the only routine exit. Name the new slot; hours become debt
   on that day. No escalation, no penalty, no comment.
2. **Pass.** A finite, visible, no-questions-asked skip. Default one per week,
   non-accumulating. The count is displayed at all times, because "2 passes left" is
   itself a motivator. Unlimited passes mean no system; zero passes mean the user breaks
   the system out of spite.
3. **Sick mode.** User-declared. All escalation silent for 24 hours, no red, no debt
   accrued. The user will get sick, and a system that punishes influenza gets killed.
4. **Watcher judgment.** §9.3.

## 9. Watcher

The policy engine. Runs on a loop, reads contract and classified reality, decides whether
to intervene.

### 9.1 Escalation ladder

Configurable list of rungs. Defaults for the first user, who explicitly asked for the
system to go hard:

| Rung | Trigger | Effector | Swipeable |
| --- | --- | --- | --- |
| 1 `wall` | Block start, nothing running | Wall display turns red | n/a — passive, must be looked at |
| 2 `notify` | +5 min | Phone notification | yes |
| 3 `light` | +30 min dead | Room light shifts | no — it is in the room |
| 4 `call` | +45 min dead, or 2nd missed block same day | Phone call | no |

The ladder exists because each rung defeats the previous rung's escape. A red screen can
be not-looked-at. A notification can be swiped. A light in the room cannot be dismissed
without getting up. A ringing phone is answered.

Escalation cancels immediately on: block started, block moved, pass used, sick mode
declared.

### 9.2 The invariant that keeps this from being cruelty

> **No escalation may be delivered without a resolvable next action.**

Every intervention carries a concrete, specific thing to do now — not "you are behind in
3341" but "3341 problem set 2, question 4." If the watcher cannot resolve a specific next
action from the contract, it **does not escalate**. This is a hard precondition in the
code and a test, not a guideline.

Rationale: shame produces avoidance, and avoidance is the failure mode being treated.
Lying in bed is already avoidance. A system that only reports failure makes the bed more
attractive, not less. Loss and recovery must appear in the same frame, always.

### 9.3 Judgment

The watcher is a model, not a cron rule, so context changes the response. A dead 07:00
block after a 03:00 finish is answered differently from a dead block after ten hours in
bed. It has the observation log; it knows which one happened.

The model may choose a **lower** rung than the ladder specifies, and must justify it in
the log. It may never choose a higher one. Discretion runs toward mercy only.

## 10. Setup wizard

First run opens a browser-based setup. It is not a convenience feature: **it is the
structural guarantee that no personal data enters the repository.** Nothing can be
hardcoded if it is collected at runtime.

Steps:

1. Choose a domain pack
2. **Learn places** — user presses "I am home now"; the current SSID is captured and
   named. Repeated for each place. No SSID is ever shipped or defaulted.
3. Declare commitments and weekly targets (pack-specific fields appear here)
4. Lay out the weekly block template
5. Configure the ladder: which rungs, what timings, how hard it is allowed to push
6. Connect effectors and test each one end to end
7. Choose the classifier backend, with the off-machine warning stated plainly
8. Optional: enable the camera presence sensor, with §12 stated plainly

Output is a single local config file, gitignored. The wizard is re-runnable.

## 11. Domain packs

The engine holds no domain knowledge. A **pack** supplies data and optional plugins.

Ships with exactly one pack: **`school`**.

| Provides | Detail |
| --- | --- |
| Commitment fields | Course code, section, instructor, meeting times |
| Grade model | Weighted items, running grade, and *what score is still needed on remaining items to reach a target* |
| Consequence chain | User-authored, config-held: the chain from unspent hours to the outcome that actually matters to them |
| Campus mode | On a place tagged `campus`, gaps between meetings are surfaced as prime blocks |

The grade model exists because the user does not want to depend on the institution's
gradebook, which updates too slowly to change behaviour.

### 11.1 Scope discipline

**Generalize the structure. Do not build the framework.**

v1 ships the engine plus one pack. No plugin API is designed for hypothetical packs. The
extension seam is exactly where the config boundary already falls, and it will be carved
properly when a second real pack exists, because two concrete cases define an interface
correctly and one imagined case does not.

A speculative plugin system is the specific way this project fails: an elegant extension
framework, delivered late, with no working instrument on the day it was needed.

## 12. Privacy and safety

The subject is the operator, which permits sensing that would be unacceptable otherwise,
and obliges strict limits.

**Camera.** Aimed at the **work seat, not the bed.** It answers "is the seat occupied,"
which is the same information the bed framing would provide, with far less exposure and
no recording of sleep. Pipeline: acquire frame → derive one boolean → discard frame.

> **No frame is written to disk, ever, and none leaves the machine.** Persisted output is
> `{desk_occupied: bool, ts}` and nothing else.

This is enforced by test: the presence sensor is exercised with a filesystem-write
assertion, and any image write fails the suite. The sensor is opt-in and disabled by
default.

**Window titles** are sensitive: they carry document names and URLs. They are stored
locally, and never transmitted unless the cloud classifier backend is explicitly enabled,
in which case only the ambiguous title is sent. A retention setting redacts titles to
their classified verdict after a configurable window.

**Location** is inferred from SSID only. No GPS, no continuous coordinates, no
third-party location service.

**Nothing is uploaded.** There is no server, no account, no telemetry.

## 13. Views

Two views over one store. They are different products and must not be one responsive
layout.

### 13.1 Wall

Runs full-screen on the 720p TV over `HDMI-1`. Read from across a room, in a second,
without intent.

- Three numbers only: **banked today**, **gone today**, **what is running now**
- The semester grid, one cell per waking hour, filling in real time
- Current escalation state
- The consequence chain, per §11 and the user's explicit request
- Enormous type, maximum contrast, no thin weights, no subtle greys — a 2011 panel has a
  tired backlight and poor viewing angles
- No interaction whatsoever

### 13.2 Phone

Served over LAN. Interactive: start, stop, move, use a pass, declare sick, answer a Tier-3
classification question, enter grade items and scores.

### 13.3 The grid

The primary visual. One cell per waking hour for the whole term, so the term is a single
physical object with a fixed size that visibly consumes itself.

| Cell | Meaning |
| --- | --- |
| filled | aligned time |
| hatched | ambient |
| grey | accounted (class, work, sleep) |
| **red** | unclaimed waking time — fills on its own |
| empty | not yet reached |

Red accumulating without the user touching anything is the entire emotional engine. The
instrument moves whether or not it is being operated, which is exactly what a passive
dashboard fails to do.

## 14. Testing

The system is time-dependent and sensor-dependent, so both are injected.

- **Clock injection.** No component reads wall-clock time directly. Every behaviour is
  reproducible at any speed.
- **Replayable logs.** A recorded observation log replays through classifier and watcher
  deterministically. A whole simulated week runs in milliseconds and asserts the exact
  intervention sequence.
- **Sensor fakes.** Every sensor has a scripted double; no test requires X11, a camera, or
  a network.
- **Classifier table tests** over titles, including the focused-versus-background rule.
- **Invariant test** for §9.2: escalation without a resolvable next action must be
  impossible to construct.
- **Privacy test** for §12: no image bytes reach disk.
- Test fixtures use obviously synthetic data — no real course codes, no real grades, no
  real network names.

## 15. Open-source posture

Public from the first commit.

- **Apache-2.0**, matching the author's other published work
- `config/` gitignored; `config.example.yaml` committed with a generic student
- Secrets in environment variables, never in config, never committed
- README states the engine-versus-pack split and what writing a second pack involves
- No personal data in source, tests, fixtures, or history
- Commit history is a readable build story, not one squashed drop

## 16. Delivery stages

Classes begin **2026-08-24**. The core must be usable that morning; a partially built
system on that date is a failure regardless of its architecture.

**Stage 1 — by Aug 24**
Engine, store, setup wizard, `school` pack, `window` / `idle` / `network` sensors,
classifier tiers 1 and 2 plus Tier-3 asking, contract with move / pass / sick, watcher
with rungs 1 and 2, the grid, wall view, phone view.

**Stage 2 — weeks 1 to 2**
`presence` sensor, grade model populated from real syllabi once published, rung 3
(light), rung 4 (call), Tier-3 learned ruleset.

**Stage 3 — unscheduled**
A second domain pack, by the author or a contributor. This is what settles the plugin
interface.

## 17. Open questions

1. **Classifier backend availability.** Local model presence on the target machine is
   unverified. Confirm before Stage 1; if absent, Tier 2 degrades to Tier 3 (ask the
   user) rather than blocking, and the tier ordering already permits this.
2. **Wall display power behaviour.** Whether the TV will be driven continuously by the
   docked laptop or wake on escalation is a usage decision, deferred until the panel is
   physically connected.
3. **Notification transport.** Reference implementation is **ntfy** — open source,
   self-hostable, no account required, HTTP POST, with a phone app. It meets the
   open-source posture in §15 because a contributor can run it without signing up for
   anything. Confirm delivery latency on the target phone during Stage 1; the `Effector`
   interface makes it swappable if it disappoints.

## 18. What was rejected, and why

| Rejected | Reason |
| --- | --- |
| Automatic tracking alone | Blind to paper work — scores the user's best hours as waste |
| Self-report alone | The user rounds up; produces no discrepancy signal |
| Passive dashboard | Fails the same way the institutional gradebook already failed: it must be opened |
| Name `proctor` | Precisely correct word, poisoned by exam-surveillance products. This is self-imposed and the name must carry that. |
| Camera aimed at the bed | Same information as the seat framing, far greater exposure, records sleep for no gain |
| Plugin framework at v1 | Guessing extension points from one example gets them wrong, and ships late |
| Hardcoded distraction blocklist | Cannot distinguish a lecture from entertainment on the same domain |
