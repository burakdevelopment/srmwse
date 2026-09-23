# SRMWSE — Space Radiation Measurement Without Specialized Equipment

**Can ordinary spacecraft engineering telemetry tell you about the radiation
environment — and how do you know when it is telling you nothing?**

Most spacecraft carry no radiation monitor. Nearly all of them carry memory
with error detection and correction (EDAC), star trackers and cameras, and all
of these report housekeeping counters that public archives keep for decades.
Those counters rise with radiation. They also rise when the spacecraft reboots
a unit, scrubs a memory, changes mode or runs a campaign. This project is about
telling the two apart from public archives alone, and about stating how sure
it can be.

> **Status: pre-alpha research. No radiation measurement is claimed.**
> Channels admitted to quantitative use: **0**. Nothing here converts a counter
> step into an error count, a flux or a dose.

**Contents** — [Summary](#summary) · [Method](#method) · [Data](#data-sources) ·
[Findings](#findings) · [Pre-registered tests](#pre-registered-tests) ·
[Literature](#position-in-the-literature) · [Not claimed](#what-is-not-claimed) ·
[Methodology](#methodology-and-rules) · [Roadmap](#roadmap-and-open-work) ·
[Usage](#installation-and-usage) · [Layout](#repository-layout) ·
[References](#references) · [License](#license-and-data-terms)

---

## Summary

| | Result | Standing |
| --- | --- | --- |
| **F4** | At Rosetta's first Earth swing-by (2005-03-04), five of six physically independent units stepped their EDAC counters within 9.5 minutes of closest approach. A five-unit window never appears in 5 000 re-timed null trials. | Discovery; exploratory |
| **F5** | Rosetta's own radiation monitor, SREM, measured a ×3 093 – 6 603 rise in its proton-only channels in the same minutes. | Independent corroboration |
| **E2** | Pre-registered test at Rosetta's three other swing-bys: no four-unit coincidence at any of them. | **Negative** by its own rule; two design faults found |
| **F6** | On a 39-day training set, SREM-measured exposure predicts the daily counter total only weakly (best Spearman ρ = +0.510). | Training set, not a test |
| **E3** | Pre-registered exposure–response test on 425 unseen days: 3 high-exposure days where the rule required 5, all from one solar event. | **Not evaluable**; ρ fell to +0.223 |
| **Traps** | A column that is not an error count (F1), a "count" that is a {0,1} flag (F3), one counter published under two ids and a 548-day gap posing as one day (F4). | Method findings |

The positive observation is real and independently corroborated; the two
pre-registered tests that followed did not confirm it, and both stay in the
record. What limits the method now is not data volume but the **number of
independent radiation events** available to test it on.

---

## Method

**The multi-device coincidence test.** A memory scrub, a firmware reload, a
thermal excursion or a failing part raises *one* unit's counter. A particle
environment is shared, so physically independent units on the same spacecraft
should respond together.

- **Group by unit, not by parameter.** The archive publishes each unit's
  counter twice — a high-rate *EDAC Sec Cntr* and a low-rate *RAM EDAC Cntr*;
  for star tracker B the two agree on all 5 788 paired samples. Counting both
  would manufacture a coincidence out of one counter, so steps are grouped by
  the unit that owns them, and a parameter with no recorded unit is refused.
- **A re-timing null.** Each parameter keeps its own sampling grid and its own
  number of increments; only *when* the increments happened is randomised. A
  unit sampled more often during a campaign keeps that advantage in the null,
  so cadence alone cannot produce a significant result.
- **Gap bounds belong to the question, not the channel.** For simultaneity, an
  increment observed across an interval wider than the 600 s coincidence
  window is set aside: its own timing uncertainty exceeds the agreement being
  claimed. For a daily rate, an increment may not cross midnight. Without a
  bound, a 548-day archive gap appeared as the largest single day in the
  record; a first, cadence-relative bound gave different answers depending on
  which unrelated quarters happened to be on disk.
- **Day ranking** against each channel's own Poisson mean is kept as a
  description, never as a detection test.
- **Confirmatory tests are pre-registered.** Hypothesis, parameters, decision
  rule and invalidation conditions are committed before the test data is
  downloaded, and are never loosened afterwards.

Fixed parameters: coincidence window 600 s, target interval closest approach
± 3 600 s, 5 000 null trials, seed 20050304.

---

## Data sources

| | Source | Access | State |
| --- | --- | --- | --- |
| **D1** | ESA GRAINS/ARES EDAC exports for seven missions, plus the Solar Orbiter SWA-HIS event log — the deposit behind Sánchez-Cano et al. (2023) | figshare, [10.25392/leicester.data.22700146.v1](https://doi.org/10.25392/leicester.data.22700146.v1), CC BY 4.0 | Pinned: `EDAC.zip`, 3 067 650 bytes, SHA-256 `bc377dad…50810f0`, publisher MD5 `0c011bfa…`. All 22 members parse, 1 728 569 records |
| **D2** | Rosetta EDAC housekeeping, `RO-X-HK-3-EDAC-V1.0` | [PDS Small Bodies Node](https://pdssbn.astro.umd.edu/holdings/ro-x-hk-3-edac-v1.0/) | 20 parameters for six units, 1 426 label/table pairs, 2004–2016; every file verified against the publisher's MD5 manifest (3 996 entries) |
| **D9** | Rosetta SREM, the spacecraft's own radiation monitor, `ro-x-srem-2-{cr1,cvp2,ear1,cr2,mars,cr3,ear2,ast1,ear3}-v1.0` | PDS Small Bodies Node | Level-2 count rates, 15 channels with published energy ranges; read for the four swing-bys and the E3 window |
| **D5** | Solar Orbiter EPD, `epd-het-sun-rates` L2, 2022-09-04 → 09-12 | [SOAR](https://soar.esac.esa.int/) TAP | 8 CDF files (11 978 499 bytes), mixed versions pinned by file name, SHA-256 computed at download; deliberately **not decoded** |
| D3, D4 | Juno ASC raw images / calibrated flux | PDS-PPI | Access checked only |
| D6 | BepiColombo BERM | ESA PSA | Access checked only |
| D7 | LRO CRaTER | PDS-PPI | Access checked only |
| D8 | GOES proton flux | NOAA NCEI | Access checked only; near-Earth context, not ground truth for distant missions |

**Cross-validation.** D1 and D2 publish the same Rosetta parameter,
`NACW0D0A`, in different formats read by independently written parsers. Over
2006 Q4: **3 206 shared timestamps, 0 value mismatches**, and D1 is a strict
subset of D2's 15 511 samples.

**Format pitfalls.** Each of these raises an error rather than being guessed:

- The GRAINS separator is a tab for most missions but a comma for TGO; it is
  detected from the header.
- ARES timestamps are day-of-year, GRAINS uses calendar dates. Recognising a
  format does not verify its time scale.
- The ARES footer's sample count is checked against the rows actually parsed,
  so a partial read cannot pass.
- The declared parameter list must match the data header. File names do not
  give the channel list: `SOL_NSM00798_SOL_NSM00799_SOL_NSM00800_event2021.txt`
  holds four channels.
- One D1 member is an operator event log, not an export; it has its own reader.
- Parameter ids are not unique across missions (`NSM00798` exists on Solar
  Orbiter and on BepiColombo), so a channel is keyed by source, mission and id.
- D2 file naming is not uniform (quarterly for AOCS/DMS, monthly for star
  trackers and cameras), so products are selected from the publisher's
  manifest, never from constructed names.
- PDS3 labels come in two dialects (a named table object; a pointer with a
  start record and repeating columns). Both are read from the label's own
  description, and a table whose bytes disagree with its label is refused.
- Missing values stay missing, never zero: zero counts, no data and zero
  exposure are different states.

**Time.** The archives *declare* their time scales — D2's user guide says
"nominal timestamp in UTC", the SREM labels say UTC — but a declaration is not
a verified clock correlation, so every reader returns `time_system: unresolved`.

**Provenance.** Raw bytes are never modified and never committed. Each download
is verified against the publisher's checksum where one exists and kept beside a
receipt: source, canonical URL, dataset and release, retrieval time (UTC),
size, SHA-256, licence, declared time system, parser version and local path.

---

## Findings

### F1 — Solar Orbiter, September 2022: the instrument memory saw it, the spacecraft memory did not

- The SWA-HIS event log (474 events) is the only Solar Orbiter channel that
  detected the 5 September 2022 solar particle event: about 13 events a day at
  baseline, **306 on 6 September**. Each line carries a memory address range:
  380 events are single-cell, one is multi-cell (on the peak day) and 93 are
  FPGA messages without an address — nothing at the thousands-of-words scale
  of a functional interrupt.
- Its displayed time equals the 2000-01-01 epoch plus the raw coarse clock in
  all 474 lines: no clock correlation has been applied, and the display drops
  the fine time.
- The main spacecraft memories `NSM00798/799/800` show **no rise** during the
  event; their large steps come two to three days later, reproducing
  Sánchez-Cano et al.'s statement that this rise is *"not related to the SEP
  event"*.
- Their raw step scale is **162–243×** the published mission average even with
  the large blocks removed, consistently across two independent windows, while
  `NCDT07B0` in the same file and window sits at 0.18×. The column does not
  count corrected errors one-for-one, so neither the `63952 → 2561` transition
  nor the ~7 600-count single-hour blocks can be read as numbers of errors.

### F2 — A second archive family: Rosetta's PDS3 EDAC archive

- 20 parameters for six units — AOCS CDMU, DMS, navigation cameras A and B,
  star trackers A and B — with redundant A/B pairs of near-identical hardware,
  up to four memories per unit, and 12 years across 1–4 AU.
- The archive's own documentation (user guide RO-SGS-UM-1002) gives the
  increment rule — the counter *"shows how many times the software has had to
  correct for a bit flip"* — declares a *"nominal timestamp in UTC"*, and
  records that the AOCS/DMS counters *"were observed to reset a number of
  times"*, a cause the Mission Operations Centre could not give. Bit width and
  scrub cadence remain undocumented.

### F3 — The redundant star-tracker pair: a flag, not a counter (partly retracted)

- `NACW1K0H` and `NACW1K2X`, named *"STR A/B Nb SEU Found"*, only ever take the
  values 0 and 1 (69 186 samples with six 1s; 69 185 with 466). They are
  per-sample flags; differencing them like a counter produces plausible, wrong
  numbers.
- Four of star tracker A's six flags in 2005 Q1 fall on 2005-01-20, the day of
  ground-level enhancement 69 — n = 4, not a detection claim.
- The record's original conclusion, that the 4–5 March 2005 pile-up (458 of
  star tracker B's 466 flags) was an operational campaign, was **wrong**: it
  was the Earth swing-by of F4. Elevated sampling ran from 2 to 6 March, but on
  3 and 6 March the same cadence produced no flags at all. The wrong
  conclusion was struck through in place, not deleted.

### F4 — Five units in nine minutes: Rosetta's 2005 Earth swing-by

Between **22:11:25 and 22:20:59 UTC on 2005-03-04**, the AOCS CDMU, the DMS,
both navigation cameras and star tracker B stepped their EDAC counters — five
of the six units reporting. ESA gives closest approach as *"around 22h10 UT"*
at 1 900 km, so the steps fall 1–15 minutes after it. The episode continues to
22:24:13 and holds the quarter's two largest single steps (+4 and +5).

`python -m srmwse coincidence --event earth_1 --min-devices <k> --trials 5000`,
2005 Q1:

| Threshold | Windows observed | Null, whole quarter | Null, closest approach ± 1 h |
| --- | --- | --- | --- |
| ≥ 3 units | 2 | p = 0.069 | p = 0.0068 |
| ≥ 4 units | 1 | p = 0.0004 | 0 of 5 000 |
| ≥ 5 units | 1 | **0 of 5 000** (95 % bound ≈ 6×10⁻⁴) | 0 of 5 000 |

A three-unit window turns up by chance in about 7 % of re-timed quarters and
would have been easy to present as evidence. A five-unit window never did.

**An independent second statistic** — how far each day sits from its
channel's own Poisson mean, over five quarters — points at the same days. The
top five days of two physically separate counters share three dates, and all
three are known radiation events:

| AOCS CDMU `NACW0D0A`, 456 days | Counts | DMS `NDMW0D0A`, 417 days | Counts |
| --- | ---: | --- | ---: |
| 2005-01-20 — GLE 69 | 50 | 2009-11-13 — Earth swing-by 3 | 17 |
| 2005-03-04 — Earth swing-by 1 | 11 | 2005-01-20 — GLE 69 | 14 |
| 2009-11-13 — Earth swing-by 3 | 8 | 2009-12-09 | 7 |
| 2005-01-17 — SEP event | 7 | 2005-03-04 — Earth swing-by 1 | 5 |
| 2007-01-26 | 6 | 2007-01-25 | 5 |

The quarters were chosen around swing-bys, so the days are not a random sample,
and the ranking is a description rather than a test. The three unlabelled days
were later checked against SREM and turned out to be ordinary fluctuation
(F6). 2005-01-20 was already reported by Sánchez-Cano et al.; recovering it
through a different archive family is a **reproduction**, which is evidence
the pipeline works.

**Still open:** star tracker A was sampled at the same cadence as B through the
swing-by (2 700 samples a day) and stepped zero times. Prime/redundant state,
power or configuration could explain it; the public archive cannot say which.

### F5 — The reference instrument was on the spacecraft all along: SREM

SREM's product labels give the proton and electron energy range of all 15
channels, citing Sandberg et al. (2012), and three of them (C1–C3) respond to
**protons only**, so the particle species is identified rather than assumed.
Peak count rate against a same-day background taken at least four hours from
closest approach:

| Channel | Species | earth_1 (1 900 km) | earth_3 (2 480 km) | earth_2 (5 301 km) | mars |
| --- | --- | ---: | ---: | ---: | ---: |
| TC3 (p > 12 MeV) | p + e⁻ | ×50 900 | ×1 922 | ×4 409 | ×1.0 |
| TC1 (p > 27 MeV) | p + e⁻ | ×2 464 | ×2 109 | ×916 | ×1.0 |
| TC2 (p > 49 MeV) | p + e⁻ | ×1 809 | ×1 477 | ×11.5 | ×1.1 |
| C1 (p 43–86 MeV) | protons only | **×3 093** | **×3 638** | ×2.9 | ×2.0 |
| C2 (p 52–278 MeV) | protons only | **×6 004** | **×6 906** | ×3.0 | ×1.4 |
| C3 (p 76–450 MeV) | protons only | **×6 603** | **×7 422** | ×1.9 | ×2.0 |
| C4 (p > 164 MeV) | p + e⁻ | ×1 102 | ×1 246 | ×1.6 | ×1.1 |

- **earth_1 and earth_3** were energetic proton encounters. At earth_1 SREM
  crosses the outer electron belt, the inner proton belt and the outer belt
  again; TC2 peaks at 22:11 at 5 089/s against a background near 2/s. The
  proton peak runs 22:09–22:21 and the EDAC steps of F4 22:11–22:24. Artifact
  grade B → **A**.
- **earth_2**, at 5 301 km, was an **electron** encounter: TC3 and TC1 rise,
  the proton-only channels do not.
- **mars**: nothing, as expected at a planet with no trapped belt (only 6 SREM
  samples fall in that interval, against 43–47 for the others).
- The counters responded to the two proton encounters and not to the electron
  one, which is what upset physics predicts. Limits: a count rate is not a flux
  (level-5 fluxes deliberately not retrieved), n = 4, and there is no
  trajectory or L-shell reconstruction yet.

### F6 — Exposure against response, and the false-alarm floor

Over the 39 days on which SREM and both continuous counters
(`NACW0D0A` + `NDMW0D0A`) are available:

- The three days F4 could not label (2007-01-25, 2007-01-26, 2009-12-09) have a
  completely flat SREM. Being far from a channel's own mean is **not** evidence
  of radiation — the project's first measurement of its false-alarm floor.
- High- and low-exposure days overlap: a quiet day reaches 11 counts, two
  high-exposure days give 1 and 2. The response is strongly non-linear:
  2005-01-18 had a proton-only median ~200× quiet and 2 counts; 2005-01-20 had a
  sharp 3 114/s TC2 peak and 64.
- Of six candidate metrics the TC2 peak (> 49 MeV, the channel Sánchez-Cano et
  al. match to Rosetta's EDAC) tracks best, at Spearman **ρ = +0.510**. At a
  threshold of 100/s every high day has ≥ 11 counts and every quiet day ≤ 11.
- The metric and threshold were chosen by looking, so this is a training set,
  not a test. Both were frozen for E3.

---

## Pre-registered tests

Both tests were written, frozen and committed **before their test data was
downloaded** — E2 in commit `524fd55` (2026-09-18) and E3 in `bc296ec`
(2026-09-19) of the development history — together with what would invalidate
them: changing code or parameters after seeing a result, tuning one event on
another, loosening the definition of a hit, excluding days, or reporting only
some outcomes. Every outcome is reported.

### E2 — Multi-device coincidence at Rosetta's other swing-bys: negative

**H1:** passing through Earth's trapped belts produces simultaneous increments
across physically distinct units near closest approach. **H0:** the
coincidence is operational (cameras on, raised sampling, mode changes) or
chance. Mars is the discriminating control: a flyby's full operational
signature, but no global magnetic field and no trapped belt.

| Event | Closest approach (UTC) | Altitude | Role | Units reporting | Outcome |
| --- | --- | --- | --- | --- | --- |
| earth_1 | 2005-03-04 ~22:10 | 1 900 km | discovery, not a test | 6 | five-unit window |
| earth_3 | 2009-11-13 07:45:40 | 2 480 km | **primary test** | 4 | no ≥ 4-unit window |
| earth_2 | 2007-11-13 20:57 | 5 301 km | secondary test | 5 | no ≥ 4-unit window |
| mars | 2007-02-25 01:54 | 250 km | negative control | 4 | no increments at all |

Frozen: ≥ 4 units within 600 s, inside closest approach ± 1 h, re-timed null
restricted to that interval, 5 000 trials, seed 20050304, Bonferroni threshold
0.05 / 3 = 0.0167. The rule for earth_3 and earth_2 both without a hit is
**H1 refuted**; that is what happened, and the rule was applied as written. The
same code returns a hit on earth_1, so the test itself was not broken.

It failed on two design faults, both found by running it:

1. **An absolute threshold does not travel.** Four units was 4/6 = 67 % at the
   discovery but 4/4 = **100 %** at earth_3, where neither camera was sampled.
   It should have been a fraction of the units reporting.
2. **The stimulus was never measured.** SREM later showed earth_2 was an
   electron encounter and Mars delivered nothing, so neither was ever a test of
   H1. A test of "does X respond to Y" must establish independently that Y
   happened, or it cannot tell an absent response from an absent stimulus.

What the intervals held: at **earth_3**, star tracker A and the AOCS CDMU
stepped between 1.5 minutes before and 16 minutes after closest approach, and
the DMS gained 13 counts in the 17-minute sample interval that contains it —
three of the four units reporting, though the DMS step is too coarsely timed
to enter a 600 s coincidence. At **earth_2**, two steps, both on star tracker
A. At **mars**, none.

The declared secondary statistic — is the closest-approach day in its
quarter's top three? — split from the primary:

| Event | `NACW0D0A` | `NDMW0D0A` |
| --- | --- | --- |
| earth_1 | 2nd (11 counts) | 2nd (5) |
| earth_3 | **1st (8)** | **1st (17)** |
| earth_2 | 60th (1) | 3rd (4) |
| mars | 53rd (1) | no counts |

The gap rule was corrected after the test (see [Method](#method)); the primary
outcome did not change.

### E3 — Exposure against response: not evaluable

**H3:** on days when SREM's TC2 (> 49 MeV) peak exceeds **100/s**, the daily
total of `NACW0D0A` + `NDMW0D0A` is higher than on other days. Exposure is
classified from SREM with the counters unread, on data never examined before:
SREM `cr2` from 2005-04-05 to 2006-06-30, with D2 quarters 2005 Q2 – 2006 Q2.
Primary statistic: one-sided permutation Mann–Whitney U (20 000 permutations,
seed 20260919), p < 0.01, with rank-biserial effect size. Declared in advance:
the high group needs **at least 5 days**, or the test is not evaluable.

| Days | |
| --- | ---: |
| Requested window | 452 |
| Absent from the archive | 21 |
| Without both counters reporting | 6 |
| **Eligible** | **425** |
| **Above 100/s** | **3** |

The three days — 2005-09-08, 09 and 10, with TC2 peaks of 1 812, 1 835 and
196/s and 168, 268 and 18 counts — are one event: the X17 flare of
7 September 2005 and the proton events after it. The SEP events seen at Earth
in May and July 2005 never lift SREM's TC2 above 5/s; Rosetta's magnetic
connection differed. The rule applied and **the primary statistic was never
computed**; the code refuses to compute it. A rank test on three days of one
flare against 422 controls would have returned a tiny p-value while
establishing nothing, which is exactly what the minimum was there to prevent.
The monotone shape (1 835 → 268, 1 812 → 168, 196 → 18, 29.7 → 4) is recorded
as an observation, not claimed.

The secondary statistic fell from Spearman **ρ = +0.510** on the training set
to **+0.223** on the test set. The training set was 15 % event days and the
test set 0.7 %: it was never a suitable sample for measuring the relationship.

One code change was made after freezing. SREM `cr2` has 21 missing days and the
product selector refused any gap; an `allow_missing` mode now drops and names
missing days for long windows. The pre-registration already stated that rule,
and it forbade narrowing the window instead.

**E4, not yet written:** the same threshold, metric and statistic on a new
sample — pool several SREM phases (the `mars` phase covers GLE 70 in December
2006; 2014–2016 fall in solar cycle 24), count **events rather than days**,
require at least five separate events, and do the power calculation from SREM
before looking at any counter.

---

## Position in the literature

Three papers were read in full or searched in full. They come from one citation
chain, so this is **not** a systematic search, and one is owed before
publication.

- **Sánchez-Cano et al. (2023)** show SEP events in the housekeeping counters of
  ESA's fleet and are the source of D1. A corrected upset adds 1, but
  multi-cell upsets and functional interrupts can produce errors *"up to
  thousands of words"*, so a count is not proportional to flux. Mission
  averages run from 0.07 counts/day (TGO) to 1.83 (Solar Orbiter), 3.08 for
  SWA-HIS and 0 for BepiColombo; Rosetta's EDAC response is matched to SREM's
  > 49 MeV channel. Event dates are known from outside. After two archive
  visits to ESTEC the authors write that the parameters are *"not properly
  described"*, that shielding CAD models for Mars Express and Rosetta are *"not
  available anymore"*, that the response differs between spacecraft, and that
  false positives need operations schedules.
- **Knutsen et al. (2021)** measure GCR modulation with EDACs on Mars Express
  and Rosetta, using the **same parameter** (`NACW0D0A`): a ~5.5-month lag at
  Mars and a radial gradient of 4.7 ± 0.8 % per AU. They resample to daily
  values, take 14-day differences, remove SEP events and separate out the Van
  Allen passages, so the swing-bys and the minute-scale simultaneity studied
  here are the residual their analysis cleans away. One channel, no
  coincidence test.
- **Jiggens et al. (2019)** calibrate dedicated instruments on the September
  2017 SPE. "EDAC" appears twice, both for one Swarm-C anomaly: an effect
  reported from a known event, the opposite direction of inference. The closest
  points are the AlphaSat memory test board (a dedicated upset experiment) and
  the Cluster solid-state-recorder bit error rates of four spacecraft side by
  side (separate spacecraft, no null).

**Where this project stands:** in none of the three is simultaneity across
physically independent units on **one** spacecraft tested against a
pre-written null.

---

## What is not claimed

- **No flux, dose or particle energy.** Counter semantics remain unresolved.
- **No confirmed dose–response relationship.** Measured exposure predicts the
  daily counter total only weakly, and the one clean separation comes from a
  single solar event.
- **The clock correlation is unproven.** Both archives *declare* UTC, and the
  headline claim is a nine-minute coincidence between two subsystems.
- **"Inner belt" rests on timing and SREM species**, not on a trajectory
  reconstruction.
- **Novelty is argued from three papers**, not from a systematic search.
- **2005-01-20 is a reproduction** of published work, not a discovery.

---

## Methodology and rules

**Evidence ladder.** Source and semantic verification → event association →
held-out relative index → flux via calibration on the same spacecraft →
energy or species discrimination. The last two are conditional on response
information. That fusing channels helps is not said until it beats the best
single channel.

**Admission.** A channel is used quantitatively only once what one count is,
its unit, reset and wrap behaviour, scrub cadence, covered memory and exposure,
operational dependence and time system are documented. Until then it is marked
`semantics unresolved` and used for exploration only. Every channel examined so
far — 36 across seven missions — is unresolved.

**Counter hygiene.** Ordering, duplicates, gaps and schema changes are checked
first. A negative step is never assumed to be a wrap (resets are documented on
Rosetta and on ExoMars TGO), and a transition that cannot be resolved
invalidates the derived count instead of being guessed.

**Artifact grades.** A — the mechanism is explained by official documentation
or a direct measurement; B — independent housekeeping corroborates it; C —
suspected from shape or timing; D — unknown, with no radiation label forced.

**Experiments.** Confirmatory tests are frozen in advance. Splits follow event
and time blocks, never rows. Nothing is tuned on test data; a change made after
seeing a result becomes a new, exploratory version. Negative controls — quiet
windows, known operational transitions, time-shuffled references — belong to
every test.

**Rules.**

1. No claim is written without opening its source; a fabricated reference is
   the most expensive error this project can make.
2. A channel with an unknown definition is not used quantitatively.
3. Negative results and wrong conclusions stay in the record.
4. A threshold is never loosened afterwards.
5. Raw data is never modified and never committed.
6. No radiation sources: public flight data and software fault injection only.
7. The word "spectrometer" is not used until energy discrimination is
   demonstrated.

---

## Roadmap and open work

The project is in **P0**. Phases open on evidence, not on dates.

| Phase | Deliverable | Gate |
| --- | --- | --- |
| P0 · Data foundation | Source manifests, channel semantics, time model, parsers, counter hygiene | Two archive families pinned (**done**: D1, D2); official semantics for any admitted channel (**open**) |
| P1 · Reproduction | Re-runnable analysis of published events | A published event reproduced by a frozen pipeline (2005-01-20 recovered; the Venus Express 2012-03-07 target, +315/day in an earlier plan, not yet) |
| P2 · Artifacts | Evidence-graded taxonomy of reset, scrub, mode, gap, clock | False-alarm effect measured at the working sensitivity |
| P3 · Statistical core | Single- and multi-channel event and relative-index models | Beats the best single channel on the same event blocks, with held-out uncertainty |
| P4 · Juno image branch | Particle-hit features from raw star-camera images | Compared against the official radiation product; may be stopped with a reason |
| P5 · Calibration and transfer | Same-spacecraft reference blackout, new-mission calibration | Reference removed from test inputs; leave-one-mission-out |
| P6 · Embedded replay | Small local inference demonstrator in Rust `no_std` or C | Golden-vector agreement with Python; no flight-worthiness claim |
| P7 · Public package | Versioned release, preprint, independent review | No unevidenced result or novelty claim in the text |

**Owed before publication**

1. Enough **independent events** to test the method at all (E4).
2. A **clock correlation** between SREM and the housekeeping timestamps.
3. A **trajectory and L-shell reconstruction** for the swing-bys.
4. **Star tracker A's operational state** during the 2005 swing-by.
5. A **systematic literature search** ("multi-device coincidence", "SEU
   coincidence", "housekeeping radiation retrieval").
6. **Official semantics**: an inventory of the AOCS/DMS resets, and parameter
   dictionaries for Solar Orbiter, Venus Express / Mars Express and Gaia.

Also open: whether Solar Orbiter's `63952 → 2561` is a 16-bit wrap or a reset,
different peak times across Gaia's channels, and Rosetta's low-sensitivity
period in December 2006.

**Publication plan.** What the evidence supports today is a methods paper —
*separating environment from artifact in opportunistic spacecraft radiation
telemetry* — built on the coincidence test, its independent corroboration, the
archive traps above and the two pre-registered tests that did not confirm it.
A second paper — *spacecraft swing-bys as natural calibration events* — waits
on a corrected test with enough independent events.

**Dependencies.** The runtime is the Python standard library (ADR 0001).
Solar Orbiter EPD ships as compressed CDF v3; one decoding dependency, `cdflib`
(itself depending only on `numpy`), was proposed in ADR 0002 — a home-made CDF
reader risks silent misreads, `solo-epd-loader` brings twelve dependencies and
bypasses the provenance layer — and then deferred, because SREM made the Solar
Orbiter branch non-critical. Nothing imports `cdflib`.

---

## Installation and usage

Python 3.12 or newer. Standard library only; no runtime dependencies.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-build-isolation
python -m unittest discover -s tests
python -m srmwse demo
```

The 283 tests and `demo` need no network. `demo` runs a synthetic counter with
deliberate faults — a reset, a zero step, a missing sample — to show they are
rejected: a software check, not a result.

Real data is downloaded on demand into `data/raw` (or `--data-dir`, or
`SRMWSE_DATA_DIR`) and outputs go to `results/`. Neither belongs in git.

```bash
# F4: all six Rosetta EDAC channels for 2005 Q1, then the coincidence test
for c in aocs_and_dms_edac_cntr navcam_a_edac_cntr navcam_b_edac_cntr \
         str_a_edac_cntr str_b_edac_cntr str_nb_seu_found; do
  python -m srmwse fetch-d2 --channel "$c" --quarters 2005_q1
done
python -m srmwse coincidence --event earth_1 --min-devices 5 --trials 5000

# F5: SREM around the same swing-by
python -m srmwse fetch-d9 --event earth_1

# E3: the pre-registered exposure-response test
python -m srmwse exposure-response
```

The E2 events are `earth_3` (quarter `2009_q4`), `earth_2` (`2007_q4`) and
`mars` (`2007_q1`).

| Command | Purpose |
| --- | --- |
| `demo` | Synthetic contract check, offline |
| `inspect-source` | Validate D1's identity, version and licence |
| `fetch-d1`, `audit-d1` | Download and pin D1; parse and inventory it |
| `channel-rates` | Raw step scale per D1 channel against the literature (F1) |
| `fetch-d2` | A named slice of the Rosetta EDAC archive |
| `fetch-d5` | Solar Orbiter EPD products, pinned, not decoded |
| `fetch-d9` | Rosetta SREM around a swing-by or over a date range |
| `coincidence` | The multi-device coincidence test and day ranking |
| `exposure-response` | The pre-registered E3 test |

If something fails: `ModuleNotFoundError: srmwse` means the environment is not
active or the editable install did not run; `No D2 products downloaded for
<quarter>` means that quarter must be fetched first; a slow archive can simply
be retried, because files already on disk are re-verified, not re-downloaded.

---

## Repository layout

```text
src/srmwse/
  cli.py             command line
  coincidence.py     coincidence test, re-timing null, gap bounds, day ranking
  stats.py           Spearman, permutation Mann–Whitney U
  time.py            timestamp contract: verified time scales only
  counters/core.py   counter hygiene and quality flags
  io/d1.py           D1 access, pinning and audit
  io/edac.py         GRAINS / ARES export parser
  io/his.py          Solar Orbiter SWA-HIS event-log parser
  io/d2.py           Rosetta EDAC archive access and unit table
  io/pds3.py         PDS3 label and fixed-width table reader
  io/d9.py           Rosetta SREM access and channel energies
  io/soar.py         Solar Orbiter archive (SOAR) access
tests/               unittest suite, offline
```

---

## References

- Sánchez-Cano et al. (2023). Solar Energetic Particle Events Detected in the
  Housekeeping Data of the European Space Agency's Spacecraft Flotilla in the
  Solar System. *Space Weather* 21(8), e2023SW003540.
  [10.1029/2023SW003540](https://doi.org/10.1029/2023SW003540) · data:
  [10.25392/leicester.data.22700146.v1](https://doi.org/10.25392/leicester.data.22700146.v1)
- Knutsen et al. (2021). Galactic cosmic ray modulation at Mars and beyond
  measured with EDACs on Mars Express and Rosetta. *A&A* 650, A165.
  [10.1051/0004-6361/202140767](https://doi.org/10.1051/0004-6361/202140767)
- Jiggens et al. (2019). In Situ Data and Effect Correlation During September
  2017 Solar Particle Event. *Space Weather* 17(1), 99–117.
  [10.1029/2018SW001936](https://doi.org/10.1029/2018SW001936)
- Sandberg et al. (2012). Unfolding and Validation of SREM Fluxes. *IEEE Trans.
  Nucl. Sci.* 59, 1105. [10.1109/TNS.2012.2187216](https://doi.org/10.1109/TNS.2012.2187216)
- Rimbot et al. (2024) — galactic cosmic rays at 0.7 AU from Venus Express
  housekeeping. [10.1016/j.pss.2024.105867](https://doi.org/10.1016/j.pss.2024.105867)
- Pettit et al. (2018) — the September 2005 solar events.
  [10.1029/2018JA025294](https://doi.org/10.1029/2018JA025294)
- Solar Orbiter EPD dataset. [10.5270/esa-5897yve](https://doi.org/10.5270/esa-5897yve)
- Pinto et al. (2022), BepiColombo BERM.
  [10.1007/s11214-022-00922-2](https://doi.org/10.1007/s11214-022-00922-2)
- Toldbo et al. (2022), Swarm star trackers.
  [10.1007/s11214-022-00925-z](https://doi.org/10.1007/s11214-022-00925-z)
- Denver et al. (2024), Juno ASC.
  [10.1007/s11214-024-01120-y](https://doi.org/10.1007/s11214-024-01120-y)

---

## License and data terms

The software is released under the MIT License, as declared in
`pyproject.toml`. Dataset terms are separate and are not covered by it: D1 is
CC BY 4.0, and every other archive has its own conditions. That is why this
repository ships downloaders rather than data.
