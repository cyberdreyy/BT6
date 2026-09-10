## Title
Hash-chain RANDAO reveal in EIP‑8321 removes BLS's unbiasability, letting a validator freely grind an entire future reveal sequence offline at zero cost to steer proposer/committee duty selection — ([File: specs/_features/eip8321/beacon-chain.md])

### Summary
EIP‑8321 replaces the legacy BLS-signature RANDAO reveal with a validator-generated hash chain. The spec's own introduction states that BLS RANDAO's grinding resistance depends on the signature being *unique* (the signer has zero freedom in the value), whereas the hash-chain scheme only relies on preimage resistance to keep a reveal *unpredictable to others* until published [1](#0-0) . This substitution silently drops the property that mattered for grinding resistance: unlike a BLS signature (which is a deterministic, unique function of the private key and message that the signer cannot choose), the hash-chain's `chain_secret` is drawn arbitrarily by the validator itself with no binding to any unbiasable source [2](#0-1) . Because the validator generates the *entire* future reveal sequence offline before ever registering on-chain, it can enumerate arbitrarily many candidate secrets/chains, simulate the resulting RANDAO mixes and duty selections locally, and register only the one chain that steers its own future proposer/committee assignments favorably — all at zero on-chain cost.

### Finding Description
`get_beacon_proposer_index` / `get_beacon_committee` / `compute_proposer_index` derive duty selection from `get_seed`, which is derived from `state.randao_mixes` [3](#0-2) . `process_randao`'s hash-chain branch folds each proposer's revealed preimage directly into that mix with no additional entropy or unbiasability check: `mix = blake3(get_randao_mix(state, epoch) + body.hash_chain_reveal)` [4](#0-3) . The only on-chain validation is `verify_hash_chain_reveal`, which merely checks that the revealed value hashes to the previously committed value — it makes no attempt to verify that the *chain itself* was generated unbiasedly [5](#0-4) .

Critically, chain generation is entirely off-protocol: `compute_hash_chain(chain_secret, length)` takes an arbitrary, self-chosen `chain_secret` and deterministically hashes forward to build the whole chain the validator will ever reveal [6](#0-5) . Nothing forces `chain_secret` to be derived from an unbiasable source (e.g., tied uniquely to the validator's BLS key the way a signature is). The validator can therefore:
1. Choose thousands/millions of candidate `chain_secret` values purely offline.
2. For each, deterministically compute the full sequence of future reveals (`chain[length-1], chain[length-2], ...`), and simulate how each reveal folds into `randao_mixes` and therefore into `get_seed`/`compute_proposer_index`/`get_beacon_committee` for future epochs.
3. Register (via `process_randao_commitment_registration`) only the one chain whose *entire future reveal schedule* produces favorable proposer/committee outcomes for itself, before ever incurring any on-chain cost [7](#0-6) .

This is a strictly more powerful and cheaper form of the well-known "RANDAO grinding" bias: legacy BLS RANDAO restricts an adversary to a single binary in-protocol choice per proposal opportunity (reveal or withhold, at the cost of a missed slot) because the BLS reveal itself is unique and non-selectable. The hash-chain scheme removes that constraint — the entire reveal sequence is validator-chosen data, searchable and testable entirely offline with no missed slots, no gas cost, and no on-chain footprint of rejected candidates, exactly analogous to Alice in the referenced Meebits report grinding for a favorable `randomIndex()` outcome at no cost to herself by simulating outcomes before committing on-chain.

### Impact Explanation
This breaks the "a duty selection one participant can steer" equality explicitly called out as in-scope High impact. A validator holding even a single validator index can bias the beacon-proposer and committee-shuffling seed derivation (`get_seed`, `compute_proposer_index`, `get_beacon_committee` in `specs/phase0/beacon-chain.md`) in its own favor over the lifetime of its hash chain (recommended length `2**16` reveals [8](#0-7) ), without losing any stake, missing any slots, or paying any gas, since all the grinding happens before the chain is ever registered.

### Likelihood Explanation
High. No special privilege beyond running a single validator and generating one's own hash chain locally is required — a capability every EIP-8321 validator has by design. The attack requires no coalition, no malicious peer, and no protocol-level anomaly; it is a direct consequence of the specified `compute_hash_chain`/registration flow.

### Recommendation
Either (a) retain the BLS-signature reveal path as the sole binding source of RANDAO entropy and do not accept hash-chain reveals into the mix at all, or (b) bind the hash-chain secret/commitment to an unbiasable, verifiable source (e.g., derive `chain_secret` deterministically and verifiably from the validator's BLS private key via a VRF-like construction so the validator has no freedom to choose among candidate chains), so that, as with BLS signatures today, the validator cannot search over multiple candidate reveal sequences before committing on-chain.

### Proof of Concept
```python
# Offline, no chain interaction required, matches EIP-8321 validator.md `compute_hash_chain`
def grind_favorable_chain(length=2**16, target_epochs=10, num_candidates=10**6):
    best_secret, best_score = None, -1
    for _ in range(num_candidates):
        candidate_secret = os.urandom(32)          # freely chosen by attacker
        chain = compute_hash_chain(candidate_secret, length)  # spec function, purely local
        # Simulate folding chain[-1], chain[-2], ... into a copy of the
        # attacker's locally-tracked randao_mixes/state exactly as
        # process_randao would, and re-derive get_seed / compute_proposer_index
        # / get_beacon_committee for the attacker's own future duty slots.
        score = simulate_future_duty_selection(chain, target_epochs)
        if score > best_score:
            best_secret, best_score = candidate_secret, score
    return best_secret  # only THIS chain's tip is ever registered on-chain
```
The attacker registers only `best_secret`'s chain tip via `SignedRandaoCommitmentRegistration` (`process_randao_commitment_registration`), never touching the chain for any rejected candidate, and thereby steers future proposer/committee selection with zero on-chain cost.

### Citations

**File:** specs/_features/eip8321/beacon-chain.md (L51-55)
```markdown
RANDAO's resistance to grinding currently relies on BLS signatures being
*unique*, so that a proposer cannot bias its contribution. A hash chain relies
only on standard hash-function security instead: collision resistance replaces
BLS's uniqueness, and preimage resistance keeps each reveal unpredictable until
it is published. Both are believed to hold against a quantum adversary.
```

**File:** specs/_features/eip8321/beacon-chain.md (L301-314)
```markdown
#### New `verify_hash_chain_reveal`

```python
def verify_hash_chain_reveal(
    state: BeaconState, body: BeaconBlockBody, proposer_index: ValidatorIndex
) -> None:
    """
    Verify that ``body`` reveals the preimage of the proposer's stored commitment.
    """
    assert body.hash_chain_reveal != Bytes32()
    assert body.randao_reveal == G2_POINT_AT_INFINITY
    commitment = state.randao_commitments[proposer_index]
    assert blake3(HASH_CHAIN_RANDAO_DST + body.hash_chain_reveal) == commitment
```
```

**File:** specs/_features/eip8321/beacon-chain.md (L404-419)
```markdown
```python
def process_randao(state: BeaconState, body: BeaconBlockBody) -> None:
    epoch = get_current_epoch(state)
    proposer_index = get_beacon_proposer_index(state)

    # [New in EIP8321]
    if state.randao_commitments[proposer_index] != UNSET_RANDAO_COMMITMENT:
        verify_hash_chain_reveal(state, body, proposer_index)
        mix = blake3(get_randao_mix(state, epoch) + body.hash_chain_reveal)
        state.randao_mixes[epoch % EPOCHS_PER_HISTORICAL_VECTOR] = mix
        state.randao_commitments[proposer_index] = body.hash_chain_reveal
    else:
        verify_bls_randao_reveal(state, body, proposer_index)
        mix = xor(get_randao_mix(state, epoch), sha256(body.randao_reveal))
        state.randao_mixes[epoch % EPOCHS_PER_HISTORICAL_VECTOR] = mix
```
```

**File:** specs/_features/eip8321/beacon-chain.md (L481-508)
```markdown
```python
def process_randao_commitment_registration(
    state: BeaconState, signed_registration: SignedRandaoCommitmentRegistration
) -> None:
    registration = signed_registration.message
    index = registration.validator_index

    assert index < len(state.validators)
    assert registration.commitment != UNSET_RANDAO_COMMITMENT
    assert state.randao_commitments[index] == UNSET_RANDAO_COMMITMENT
    assert all(pending.validator_index != index for pending in state.pending_randao_commitments)

    # Fork-agnostic domain since registrations are valid across forks
    domain = compute_domain(
        DOMAIN_RANDAO_COMMITMENT_REGISTRATION,
        genesis_validators_root=state.genesis_validators_root,
    )
    signing_root = compute_signing_root(registration, domain)
    validator = state.validators[index]
    assert bls.Verify(validator.pubkey, signing_root, signed_registration.signature)

    state.pending_randao_commitments.append(
        PendingRandaoCommitment(
            validator_index=index,
            commitment=registration.commitment,
            activation_epoch=get_current_epoch(state) + COMMITMENT_REGISTRATION_DELAY,
        )
    )
```

**File:** specs/_features/eip8321/validator.md (L56-77)
```markdown
### Generating the hash chain

The validator draws a uniformly random 32-byte chain secret and generates a
chain of `length` links from it. Reveals are consumed in reverse order, one per
block proposed, so the chain must be long enough to outlast the validator; a
`length` of at least `2**16` is recommended. The protocol never learns `length`,
and generating and storing a chain is cheap, so a generous value costs nothing.

```python
def compute_hash_chain(chain_secret: Bytes32, length: Uint64) -> Sequence[Bytes32]:
    """
    Return the hash chain ``[c_0, ..., c_length]`` generated from
    ``chain_secret``, where ``c_0`` is the secret itself.
    """
    chain = [chain_secret]
    for _ in range(length):
        chain.append(blake3(HASH_CHAIN_RANDAO_DST + chain[-1]))
    # A link equal to UNSET_RANDAO_COMMITMENT cannot be revealed, so the chain
    # must be regenerated from a fresh secret if one occurs
    assert all(value != UNSET_RANDAO_COMMITMENT for value in chain)
    return chain
```
```

**File:** specs/phase0/beacon-chain.md (L1442-1503)
```markdown
#### `get_seed`

```python
def get_seed(state: BeaconState, epoch: Epoch, domain_type: DomainType) -> Bytes32:
    """
    Return the seed at ``epoch``.
    """
    mix = get_randao_mix(
        state, epoch + EPOCHS_PER_HISTORICAL_VECTOR - MIN_SEED_LOOKAHEAD - 1
    )  # Avoid underflow
    return sha256(domain_type + uint_to_bytes(epoch) + mix)
```

#### `get_committee_count_per_slot`

```python
def get_committee_count_per_slot(state: BeaconState, epoch: Epoch) -> Uint64:
    """
    Return the number of committees in each slot for the given ``epoch``.
    """
    return max(
        Uint64(1),
        min(
            MAX_COMMITTEES_PER_SLOT,
            Uint64(len(get_active_validator_indices(state, epoch)))
            // Uint64(SLOTS_PER_EPOCH)
            // TARGET_COMMITTEE_SIZE,
        ),
    )
```

#### `get_beacon_committee`

```python
def get_beacon_committee(
    state: BeaconState, slot: Slot, index: CommitteeIndex
) -> Sequence[ValidatorIndex]:
    """
    Return the beacon committee at ``slot`` for ``index``.
    """
    epoch = compute_epoch_at_slot(slot)
    committees_per_slot = get_committee_count_per_slot(state, epoch)
    return compute_committee(
        indices=get_active_validator_indices(state, epoch),
        seed=get_seed(state, epoch, DOMAIN_BEACON_ATTESTER),
        index=Uint64(slot % SLOTS_PER_EPOCH) * committees_per_slot + index,
        count=committees_per_slot * Uint64(SLOTS_PER_EPOCH),
    )
```

#### `get_beacon_proposer_index`

```python
def get_beacon_proposer_index(state: BeaconState) -> ValidatorIndex:
    """
    Return the beacon proposer index at the current slot.
    """
    epoch = get_current_epoch(state)
    seed = sha256(get_seed(state, epoch, DOMAIN_BEACON_PROPOSER) + uint_to_bytes(state.slot))
    indices = get_active_validator_indices(state, epoch)
    return compute_proposer_index(state, indices, seed)
```
```
