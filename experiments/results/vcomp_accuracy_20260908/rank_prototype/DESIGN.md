# Proposal: discrete count CDF with feasible integer split allocations

Status: design only. No implementation, production source change, or DB experiment
is requested by this document. The N−1 prototype in `REPORT.md` is insufficient:
it fixes dense endpoints while leaving local capacity and sibling overlap errors.

The smallest coherent change is to make merge, split, and reconstruction use one
discrete count convention, with an integer allocation certificate beside the
existing approximate model. Adjusting the global PLR slope alone cannot provide
these guarantees.

## 1. One meaning for count, rank, and boundaries

Use integer **edges** for the counting domain. For an inclusive key envelope
`[lo, hi]`, its edge domain is `[lo, hi+1]`. Define

```
F(b) = number of reconstructed distinct keys strictly less than edge b
F(lo) = 0
F(hi+1) = N
0 <= F(b+1) - F(b) <= 1
```

F is integer-valued at integer edges. The final condition is essential: each
integer key can contribute at most one distinct key. N is cardinality, whereas
valid key ranks are `r = 0, ..., N-1`. These are different quantities; an endpoint
count of N is correct at `hi+1`, not a key-rank prediction of N at hi.

Every interval used to count or allocate mass is half-open `[a,b)`. Its integer
capacity is `C=b-a`, and an actual key k belongs to exactly one interval satisfying
`a <= k < b`. If an existing inclusive range-count API is reused, call it with
`[a,b-1]` after handling the empty interval and wide edge type explicitly.
Adjacent intervals must never both count their shared edge key.

For inversion, define the rank select operation by the first count crossing:

```
Select(r) = smallest key k such that F(k+1) >= r+1
```

This is not nearest-segment inversion or rounding a real-valued inverse. A flat
gap has zero mass and no rank; inversion skips it. At an exact segment-prefix tie,
the rank belongs to the following positive-mass segment. In cumulative-prefix
search, select the first segment whose end count is strictly greater than r.

## 2. Build a bounded interval model, then reconcile integer mass

Build a sorted edge partition from input descriptor boundaries, PLR support
boundaries, and selected range-sketch boundaries. Convert inclusive ends to wide
`end+1` before arithmetic. The number of temporary intervals depends on descriptor
segments and sketch metadata, not the number of original keys.

For each interval j retain:

- Its half-open bounds and exact integer capacity `C_j`.
- A nonnegative raw weight `w_j` from a consistently half-open range sketch
  estimate or a difference of input count CDFs.
- A lower bound `l_j` for mandatory distinct witness keys, if any.
- Whether zero support is proven or merely estimated.

A sketch with zero retained samples does not prove the interval empty. Likewise,
a zero estimated density is not sufficient to forbid allocation there. A hard
zero-capacity support interval is appropriate only when every overlapping input
has trustworthy evidence that it contains no keys there. True empty gaps in raw
sorted-key training can be preserved if that evidence survives descriptor updates;
an approximate merged model alone does not automatically provide such evidence.

For the minimal design, preserve the known global minimum and maximum as
singleton witness intervals of mass 1, deduplicating them when they are equal.
An endpoint is an original-membership witness only if it was actually observed
or that provenance was preserved; an inferred PLR/file bound is not such proof.
Certified reconstructed endpoints can instead be witnesses of the modeled set.
Optionally protect all retained KMV sample keys the same way. This adds only
metadata-sized work, preserves observed membership witnesses, and makes a complete
sketch an exact small-set oracle. It does not make an incomplete sketch exact.
An explicit interval/segment budget is needed if protected samples are persisted;
known witness constraints must not silently disappear during compression.

Choose the requested global cardinality N separately from interval allocation:

1. Use an exact count when the input union is fully represented by complete
   sketches, or exact disjointness and exact input counts prove the sum.
2. Otherwise use the chosen sketch estimate and record its raw value, uncertainty,
   rounding, and any feasibility projection. The current KMV threshold convention
   and the asymmetric clipping issue need their own estimator decision.
3. Treat a previously estimated descriptor count as an estimate, not automatically
   a mathematically valid upper bound on the original key union. Hard bounds must
   come from actual evidence: known distinct witnesses, proven support capacity,
   or exact input counts/operation bounds when available.

Allocate integers satisfying

```
l_j <= m_j <= C_j
sum(m_j) = N
```

One implementable policy is capped weighted allocation: find fractional allocations
near `lambda * w_j` within each interval's lower and upper bounds, freeze saturated
intervals, then distribute the integer residual by largest fractional remainder.
Break equal-remainder ties by ascending interval edge. Do not independently round
all interval estimates. All final counts and residual checks must use wide integer
arithmetic; floating-point weights must not decide whether an infeasible allocation
is silently accepted.

If all unsaturated weights are zero but residual mass remains, use an explicitly
recorded capacity-weighted fallback over admissible support. This is a distribution
approximation, not evidence of actual membership. It must still satisfy the exact
allocation constraints.

If `sum(l_j) > N` or `N > sum(C_j)`, return an explicit infeasible-model result.
A caller may choose a separately documented revised count policy, but must not keep
the old descriptor count and let materialization discard the excess. Record both
requested and accepted totals if any policy revises N.

## 3. Realize each allocated interval without rounding collisions

Given an interval `[a,a+C)` with integer mass m and preceding count P, use

```
F(b) = P + floor(m * (b-a) / C),  a <= b <= a+C
```

For `m=0`, its CDF is flat and it is never selected. For `m>0`, local rank
`t=r-P`, `0 <= t < m`, has the exact inverse

```
Select(r) = a + ceil((t+1) * C / m) - 1
```

Because `m<=C`, consecutive selected keys are strictly increasing and remain
inside the interval. Exactly m keys are generated. This concrete uniform-within-
interval realization is a minimal feasibility model; finer intervals can retain
more of the PLR shape. The interval weights affect where cardinality is allocated,
while the integer certificate determines whether the resulting key set is valid.

This formula places a one-key unanchored interval at its final key. That is a
deterministic modeling choice, not an accuracy claim. Singleton witness intervals
avoid arbitrary movement of known minimum/maximum/sample keys. If a different
within-interval placement is desired, its CDF/select pair must be derived together
and prove the same capacity and ordering properties.

Persist enough exact information for the integer certificate: inclusive key
bounds or a wide span, allocated mass, integer prefix count, and any phase needed
after slicing. Approximate double slope/intercept alone cannot certify these
properties across all uint64 keys. This is O(number of model intervals), not an
array of reconstructed keys. An implementation can keep PLR for estimating weights
while using the integer certificate for split and reconstruction.

## 4. Split rank intervals, preserve the same selection function

Keep size/grandparent cut policy separate from count correctness. It produces
strictly increasing integer cuts inside `(0,N)`. Every child receives a nonempty
rank interval `[p,q)` and exactly `q-p` entries. Its bounds are

```
child_min = Select(p)
child_max = Select(q-1)
```

Since Select is strictly increasing, sibling bounds cannot overlap, and
`q-p <= child_max-child_min+1`. Child materialization must emit the same parent
`Select(p), ..., Select(q-1)` values; it must not independently renormalize a
clipped segment or round its inverse again.

In particular, slicing an interval must preserve its rational denominator and
floor phase. For a parent cell with origin a, mass m, span C, and a clipped origin
`a'`, let

```
phase = (m * (a'-a)) mod C
local_count_increment(b) = floor((phase + m*(b-a')) / C)
```

Together with the child's integer starting count, this reproduces the original
CDF on the clipped cell. Resetting the phase or replacing m/C by
`child_mass/child_span` changes the selected keys and can reintroduce collisions.
Equivalent storage of a parent cell plus an exact rank/key slice is also valid.

Grandparent boundaries should be converted using the same F, under an explicit
before-key/after-key convention, instead of rounding `Predict`. The policy may
retain duplicate boundary events to preserve its transition counter, but the
emitted rank cuts must be strictly increasing. Zero-size cuts are ignored without
losing the policy's boundary-event bookkeeping.

Output bucket counts should come from the certified child CDF difference
`F_child(b)-F_child(a)` on a disjoint half-open partition covering the child domain.
They then sum exactly to the child's modeled cardinality. Keep any fresh KMV
range estimate in a separately named diagnostic field; do not overwrite the
certified mass with an independently rounded estimate. Conversely, do not treat
this modeled mass as a proven upper bound on original logical-key cardinality.
The original-set versus modeled-set distinction applies to both counts and samples.

## 5. Empty cases and full uint64 domain

- N=0 produces no files and requires no inversion. Nonempty proven witnesses make
  N=0 infeasible, rather than a silently empty output.
- N=1 has rank 0. If one actual key is known, store/select that singleton exactly.
  Two distinct mandatory extrema make N=1 infeasible; do not average them.
- Use `unsigned __int128` for edges, capacities, products, and accumulated counts.
  The edge after `UINT64_MAX` is the representable sentinel `2^64` in that type.
  Cast before adding one: `u128(hi)+1`, not `u128(hi+1)`.
- Avoid `(lo+hi)/2` and `(product+divisor-1)/divisor`. Use a safe difference-based
  midpoint if needed, and quotient plus a nonzero-remainder test for ceil division.
  Reject division by zero. Convert a selected edge-derived key to uint64 only
  after proving it is at most `UINT64_MAX`.
- Current uint64 cardinality fields cannot represent N=`2^64`. Detect and reject
  that unsupported cardinality or widen the API explicitly. Do not wrap a total
  while summing input descriptors. With N<=`UINT64_MAX` and C<=`2^64`, the products
  in the formulas above fit unsigned 128-bit arithmetic.
- Any model compression must preserve integer endpoints, mass, phase, and the
  per-key capacity bound. Equal approximate slopes alone are insufficient reason
  to merge cells.

## 6. What this guarantees, and what still needs an estimator/identity design

Without full original keys, the proposed certificate can guarantee, for one
accepted merge result:

- The chosen total N is allocated exactly, and child totals sum to N.
- Reconstruction emits exactly N distinct integer keys within admissible support.
- Child ranges are ordered, nonoverlapping, and large enough for their counts.
- Proven empty gaps and explicitly protected membership witnesses are preserved.
- Slicing and reconstruction agree independently of floating-point/FMA behavior.

It cannot, from incomplete sketch/model metadata alone, guarantee that N equals
the true original union cardinality, that every reconstructed key is an original
key, or that the final DB's distinct count across overlapping levels equals the
trace's exact distinct count. Different original sets can share the same bounded
descriptor. A merge-local count certificate does not resolve cross-level duplicate
identities or sequence-number semantics.

Also keep the meaning of KMV samples explicit: sketches of original logical input
keys are not automatically sketches of the synthetic keys emitted by Select.
Repartitioning original samples by synthetic output file bounds can lose samples
or logical coverage in intervening gaps; protecting retained sample keys prevents
that specific observed-sample loss, but cannot reveal omitted unsampled keys.
Rebuilding an exact bottom-k hash sketch of synthetic output keys generally needs
enumeration or a separately justified sampling algorithm. Do not claim the rank
certificate solves repeated-merge sketch bias or metadata/identity consistency.

## 7. Acceptance tests before any production proposal

Use the current prototype's complete-sketch corpus first, with PLR errors 0 and 8
and multiple split targets. Add exact-empty inputs, duplicate singleton inputs,
two distinct extrema, disjoint dense ranges, interleaved disjoint sets, complete
overlap, sparse clusters, flat gaps, tiny intervals with large requested mass,
shared boundaries, and keys near both zero and `UINT64_MAX`.

Required exact checks are `sum(m_j)=N`, `m_j<=C_j`, integer CDF increments in
{0,1}, valid CDF/select inverse relations, strictly increasing reconstruction,
nonoverlapping sibling bounds, child sum N, and exact equality between parent
reconstruction and concatenated child reconstruction. Repeat split/recombine to
detect lost floor phase. Infeasible cases must fail explicitly.

Measure original membership overlap, original sample retention, and true union
count separately. For incomplete sketches, report those as accuracy measurements,
along with raw estimator error, clipping/projection, actual retained samples,
theta, fallback intervals, and merge depth. Increasing sample budget or changing
PLR error is not a substitute for the exact feasibility checks above.
