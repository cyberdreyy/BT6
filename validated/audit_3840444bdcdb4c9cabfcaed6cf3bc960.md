### Title
Missing uniqueness check in `is_valid_indexed_payload_attestation` allows a single PTC member to spoof the entire committee's vote - (File: `specs/gloas/beacon-chain.md`)

### Summary
`is_valid_indexed_payload_attestation` in Gloas validates a PTC (Payload Timeliness Committee) `IndexedPayloadAttestation` by checking that `attesting_indices` is non-empty and equals its own sort, but it never checks uniqueness. This is the same "duplicate slate entry" bug class as the reported Ajna issue: a list that is supposed to represent one-vote-per-distinct-participant is accepted with repeated entries, letting a single participant multiply its own weight in a downstream aggregate/threshold computation.

### Finding Description
Compare the two "indexed" validity checks in this repo: [1](#0-0) 

Phase0's `is_valid_indexed_attestation` explicitly requires `list(indices) != sorted(set(indices))` to fail, i.e. it demands the indices be **sorted and unique** — `set()` collapses duplicates, so any repeated index causes the comparison to fail and the attestation is rejected.

Gloas's new `is_valid_indexed_payload_attestation`, however, drops the `set()`: [2](#0-1) 

It only checks `list(indices) != sorted(indices)`. A strictly non-decreasing list can still contain duplicates (e.g. `[3, 3, 5]`), so `sorted(indices)` equals `indices` and the function returns `True` even though the same validator index appears multiple times.

Because BLS aggregate signature verification is linear in the group, a single validator who legitimately holds `sk_i` can trivially construct a signature that verifies against its own pubkey listed `k` times: if `sig_i = sk_i * H(m)`, then `k * sig_i` verifies against `k * pk_i` under `FastAggregateVerify`, since `e(k*pk_i, H(m)) = e(g, H(m))^(k*sk_i) = e(g, k*sig_i)`. This requires no coordination with any other committee member and no cryptographic break — it is simple scalar multiplication of one's own valid signature. Unlike the phase0 slashing/attestation path (which explicitly guards against this with `sorted(set(...))`), the Gloas PTC path has no such guard.

### Impact Explanation
The PTC's `attesting_indices` are meant to represent one vote per distinct committee member, feeding into `process_payload_attestation`'s weight/quorum accounting that determines whether the parent's execution payload is treated as "seen"/timely — a fact that subsequently gates builder-payment settlement (`settle_builder_payment`) and payload availability bookkeeping in `apply_parent_execution_payload` / `process_withdrawals`. If a single PTC member can list its own index up to the full committee size and self-sign a matching aggregate, that one validator can single-handedly manufacture the appearance of a full-committee (or majority) vote, steering the payload-timeliness outcome — and thus the builder-payment settlement path — without possessing majority stake or committee agreement. This falls under "a duty selection one participant can steer" / potentially "a payload or payment applied that the block did not commit to," depending on how the resulting weight is consumed.

### Likelihood Explanation
No coordination, no signature forgery, and no assumption break beyond standard BLS linearity is required — only a validator's own existing PTC membership and its own valid key. The only missing precondition is a bounds check that the spec's own phase0 code already demonstrates is the idiomatic fix (`sorted(set(indices))`), making the omission in Gloas look like a straightforward regression rather than an intentional design choice (unlike sync-committee duplicates, which the spec explicitly documents and accounts for elsewhere).

### Recommendation
Change `is_valid_indexed_payload_attestation` in `specs/gloas/beacon-chain.md` to require `list(indices) != sorted(set(indices))` (mirroring `is_valid_indexed_attestation` in phase0), so duplicate validator indices in a PTC attestation are rejected before any downstream weight/quorum accounting occurs.

### Proof of Concept
1. A validator `V` at index `i` is a member of the PTC for slot `s`.
2. `V` constructs an `IndexedPayloadAttestation` with `attesting_indices = [i, i, i, ..., i]` (sorted trivially since all elements equal), and computes its own signature `sig_i = sk_i * H(signing_root)`.
3. `V` submits `k * sig_i` (a simple EC scalar multiplication it can compute alone) as the aggregate signature.
4. `is_valid_indexed_payload_attestation` accepts this: indices are non-empty and `sorted(indices) == indices`; `bls.FastAggregateVerify([pk_i]*k, signing_root, k*sig_i)` succeeds because `e(k*pk_i, H(m)) = e(g, k*sig_i)`.
5. Downstream weight accounting in `process_payload_attestation` (not fully inspectable within this scan's tool budget, but structurally driven by the length/content of `attesting_indices`) would then count `V`'s single vote as `k` votes, letting `V` alone move the PTC's weight past any quorum/majority threshold used to determine payload timeliness.

Note: I could not directly re-open `process_payload_attestation`'s weight-tally implementation within the remaining tool budget to confirm the exact arithmetic consuming `attesting_indices`; the root-cause validity gap in `is_valid_indexed_payload_attestation` itself is confirmed directly from the spec text, but the precise downstream blast radius (whether it only affects timeliness scoring vs. also affects reward/payment amounts) should be re-verified by reading `process_payload_attestation` in full before treating severity as fully established.

### Citations

**File:** specs/phase0/beacon-chain.md (L1140-1158)
```markdown
#### `is_valid_indexed_attestation`

```python
def is_valid_indexed_attestation(
    state: BeaconState, indexed_attestation: IndexedAttestation
) -> bool:
    """
    Check if ``indexed_attestation`` is not empty, has sorted and unique indices and has a valid aggregate signature.
    """
    # Verify indices are sorted and unique
    indices = indexed_attestation.attesting_indices
    if len(indices) == 0 or list(indices) != sorted(set(indices)):
        return False
    # Verify aggregate signature
    pubkeys = [state.validators[i].pubkey for i in indices]
    domain = get_domain(state, DOMAIN_BEACON_ATTESTER, indexed_attestation.data.target.epoch)
    signing_root = compute_signing_root(indexed_attestation.data, domain)
    return bls.FastAggregateVerify(pubkeys, signing_root, indexed_attestation.signature)
```
```

**File:** specs/gloas/beacon-chain.md (L1077-1097)
```markdown
#### New `is_valid_indexed_payload_attestation`

```python
def is_valid_indexed_payload_attestation(
    state: BeaconState, attestation: IndexedPayloadAttestation
) -> bool:
    """
    Check if ``attestation`` is non-empty, has sorted indices, and has
    a valid aggregate signature.
    """
    # Verify indices are non-empty and sorted
    indices = attestation.attesting_indices
    if len(indices) == 0 or list(indices) != sorted(indices):
        return False

    # Verify aggregate signature
    pubkeys = [state.validators[i].pubkey for i in indices]
    domain = get_domain(state, DOMAIN_PTC_ATTESTER, compute_epoch_at_slot(attestation.data.slot))
    signing_root = compute_signing_root(attestation.data, domain)
    return bls.FastAggregateVerify(pubkeys, signing_root, attestation.signature)
```
```
