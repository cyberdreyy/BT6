### Title
Hash-chain RANDAO reveal lets a validator freely choose its own entropy contribution, enabling proposer/committee duty grinding - (File: `specs/_features/eip8321/beacon-chain.md`)

### Summary
The EIP-8321 hash-chain RANDAO feature replaces the BLS-signature RANDAO reveal — a value that is *cryptographically forced* to be a unique, unpredictable function of the validator's secret key and the epoch — with a reveal that is simply an arbitrary 32-byte preimage the validator generated for itself off-chain. Since the validator constructs its entire hash chain up front and only needs to prove chain membership (not any binding to its key or the epoch), a validator can pre-select the exact bytes that will be folded into `randao_mix` on every future slot it proposes. Because `randao_mix` seeds `get_beacon_proposer_index` and `get_beacon_committee` via `get_seed`, this gives the registering validator steerable influence over future proposer/committee duty selection network-wide, without any equivocation, slashing risk, or majority stake requirement.

### Finding Description
In the legacy RANDAO scheme, `process_randao` in `specs/phase0/beacon-chain.md` requires:
```python
signing_root = compute_signing_root(epoch, get_domain(state, DOMAIN_RANDAO))
assert bls.Verify(proposer.pubkey, signing_root, body.randao_reveal)
mix = xor(get_randao_mix(state, epoch), sha256(body.randao_reveal))
``` [1](#0-0) 
The contributed value `body.randao_reveal` is a BLS signature over the epoch — deterministic and unique for a given (key, epoch) pair, so the proposer cannot choose its value; the only degree of freedom it has is to withhold the block entirely (the well-known, already-documented "RANDAO biasing by non-proposal" issue).

The EIP-8321 feature (`specs/_features/eip8321/beacon-chain.md`) replaces this with a hash-chain commit/reveal:
```python
def process_randao_commitment_registration(
    state: BeaconState, signed_registration: SignedRandaoCommitmentRegistration
) -> None:
    ...
    state.pending_randao_commitments.append(
        PendingRandaoCommitment(
            validator_index=index,
            commitment=registration.commitment,
            activation_epoch=get_current_epoch(state) + COMMITMENT_REGISTRATION_DELAY,
        )
    )
``` [2](#0-1) 
and, on reveal:
```python
def verify_hash_chain_reveal(
    state: BeaconState, body: BeaconBlockBody, proposer_index: ValidatorIndex
) -> None:
    assert body.hash_chain_reveal != Bytes32()
    assert body.randao_reveal == G2_POINT_AT_INFINITY
    commitment = state.randao_commitments[proposer_index]
    assert blake3(HASH_CHAIN_RANDAO_DST + body.hash_chain_reveal) == commitment
``` [3](#0-2) 
```python
if state.randao_commitments[proposer_index] != UNSET_RANDAO_COMMITMENT:
    verify_hash_chain_reveal(state, body, proposer_index)
    mix = blake3(get_randao_mix(state, epoch) + body.hash_chain_reveal)
    state.randao_mixes[epoch % EPOCHS_PER_HISTORICAL_VECTOR] = mix
    state.randao_commitments[proposer_index] = body.hash_chain_reveal
``` [4](#0-3) 

The only thing enforced on-chain is that `body.hash_chain_reveal` hashes to the stored commitment; nothing constrains the *content* of the preimage to be a function of the validator's private key, the epoch, or any other unpredictable/externally-fixed input. The chain generation itself happens entirely off-chain, under the sole control of the registrant:
> "Each validator generates a hash chain off-chain and registers its tip as a commitment." [5](#0-4) 

This means a validator can pick a full chain of arbitrary 32-byte words `w_0, w_1, …, w_n` (chosen by grinding a large search space off-chain, entirely at its leisure and with no time pressure or on-chain footprint) and then simply register `commitment = blake3(DST + w_0)`, walking the chain backward as it proposes. Every future value it contributes to `randao_mix` is therefore a value of its own choosing rather than an unpredictable, key-bound quantity. Because `randao_mix` (via `get_seed`) drives `get_beacon_proposer_index` and `get_beacon_committee`:
```python
def get_beacon_proposer_index(state: BeaconState) -> ValidatorIndex:
    epoch = get_current_epoch(state)
    seed = sha256(get_seed(state, epoch, DOMAIN_BEACON_PROPOSER) + uint_to_bytes(state.slot))
``` [6](#0-5) 
```python
return compute_committee(
    indices=get_active_validator_indices(state, epoch),
    seed=get_seed(state, epoch, DOMAIN_BEACON_ATTESTER),
    ...
)
``` [7](#0-6) 
the registrant gets a direct, deterministic lever over the seed used for every future epoch's proposer and committee assignment — for the whole network, not just itself — every time it proposes. This breaks the core unpredictability/unbiasability property RANDAO is supposed to provide, going beyond the known "proposer can skip a slot to bias one bit" limitation of classic RANDAO: here the registrant can inject any value it likes, an unbounded grinding surface prepared entirely off-chain with `COMMITMENT_REGISTRATION_DELAY` (3 epochs) of advance notice and no way for the protocol to detect or penalize the selection, since any 32-byte value is a legitimate chain element.

The spec's own security note only addresses a narrower concern (a second validator copying someone else's commitment), and does not address the fact that the legitimate registrant's chain content is unconstrained:
> "Commitments carry no identity and are copyable, so a validator may register another's commitment. Doing so is self-defeating..." [8](#0-7) 
This note never claims the registrant's own chosen preimages are unbiased or unpredictable — it only rules out a different, less relevant, self-defeating attack.

### Impact Explanation
This is a "duty selection one participant can steer" class issue. A single validator (no majority stake, no coalition required) can pre-grind a hash chain such that its sequence of reveals biases the RANDAO mix and thereby the seed used for proposer and committee selection over many future epochs in its favor (e.g., increasing its own proposer/attester selection odds, or those of a colluding party it wants to favor), each time it is scheduled to propose. This matches the High-impact category "a duty selection one participant can steer."

### Likelihood Explanation
Likelihood is high for any validator that adopts hash-chain RANDAO: the grinding work is done entirely off-chain, before registration, with no time constraint (the `COMMITMENT_REGISTRATION_DELAY` of 3 epochs is meant to prevent a different attack — pre-knowing the activation epoch — but does nothing to constrain the *value* the registrant picks for chain elements). There is no signature or key-derivation binding on the raw chain content, so this is not a probabilistic/borderline attack; it is a direct consequence of the design as specified.

### Recommendation
Bind each hash-chain link's raw preimage to something the validator cannot freely choose, e.g., derive it deterministically from a BLS/PQ signature over the target epoch and chain index (as the legacy scheme did), or require the registrant to commit to the entire chain via a VDF or other non-parallelizable/verifiable-unpredictable construction so its content cannot be selected for a favorable outcome. At minimum, require that the accumulator function used in `process_randao` (`blake3(get_randao_mix(state, epoch) + body.hash_chain_reveal)`) combine the reveal with contributions that are independently verified to be unbiased, and add a normative note acknowledging that raw preimages are attacker-chosen and must therefore be validated against a verifiable-random construction before being trusted as unbiased entropy.

### Proof of Concept
1. Validator V (any active validator, no special stake requirement) locally generates `N` random 32-byte words `w_0 … w_{N-1}`, biased/searched off-chain so that the sequence of accumulator states `m_i = blake3(m_{i-1} + w_i)` for i corresponding to V's anticipated future proposal slots yields seeds (via `get_seed`) that favor V's own future proposer/committee selection (this is a pure off-chain grinding search against the public `get_seed`/`compute_committee` functions, with no on-chain cost or feedback needed since the mix's evolution besides V's own injected words is public/predictable one epoch ahead within `MIN_SEED_LOOKAHEAD`).
2. V computes `commitment = blake3(HASH_CHAIN_RANDAO_DST + w_{N-1})` and submits `SignedRandaoCommitmentRegistration{validator_index: V, commitment}` signed with its pubkey, which passes `process_randao_commitment_registration` unconditionally as long as V is unregistered [2](#0-1) .
3. After `COMMITMENT_REGISTRATION_DELAY` epochs, each time V proposes, it reveals the next word in its precomputed chain (`hash_chain_reveal = w_i`), which passes `verify_hash_chain_reveal` since `blake3(DST + w_i) == commitment_{i+1}` by construction [3](#0-2) , and the mix is updated to the attacker-chosen `blake3(prev_mix + w_i)` [4](#0-3) .
4. This attacker-controlled mix feeds `get_seed`, and hence `get_beacon_proposer_index`/`get_beacon_committee` for future epochs [6](#0-5) [7](#0-6) , giving V a steerable influence on duty selection each time it proposes, indefinitely, without ever equivocating or risking slashing.

### Citations

**File:** specs/phase0/beacon-chain.md (L1476-1489)
```markdown
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

**File:** specs/phase0/beacon-chain.md (L1492-1500)
```markdown
#### `get_beacon_proposer_index`

```python
def get_beacon_proposer_index(state: BeaconState) -> ValidatorIndex:
    """
    Return the beacon proposer index at the current slot.
    """
    epoch = get_current_epoch(state)
    seed = sha256(get_seed(state, epoch, DOMAIN_BEACON_PROPOSER) + uint_to_bytes(state.slot))
```

**File:** specs/phase0/beacon-chain.md (L2307-2316)
```markdown
def process_randao(state: BeaconState, body: BeaconBlockBody) -> None:
    epoch = get_current_epoch(state)
    # Verify RANDAO reveal
    proposer = state.validators[get_beacon_proposer_index(state)]
    signing_root = compute_signing_root(epoch, get_domain(state, DOMAIN_RANDAO))
    assert bls.Verify(proposer.pubkey, signing_root, body.randao_reveal)
    # Mix in RANDAO reveal
    mix = xor(get_randao_mix(state, epoch), sha256(body.randao_reveal))
    state.randao_mixes[epoch % EPOCHS_PER_HISTORICAL_VECTOR] = mix
```
```

**File:** specs/_features/eip8321/beacon-chain.md (L57-62)
```markdown
Each validator generates a hash chain off-chain and registers its tip as a
commitment. When proposing, the validator reveals the preimage of its currently
stored commitment; the state verifies the chain step, folds the raw preimage
into the RANDAO accumulator, and stores the preimage as the validator's new
commitment. The state therefore holds one 32-byte word per validator and walks
one link back per proposal.
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

**File:** specs/_features/eip8321/beacon-chain.md (L395-402)
```markdown
*Note*: The hash-chain path folds in the raw reveal with a hash accumulator
rather than an `xor`. Commitments carry no identity and are copyable, so a
validator may register another's commitment. Doing so is self-defeating: the
copier does not hold the preimage, so it cannot propose at all until its victim
reveals, forfeiting every slot it is assigned in the meantime. Even once the
victim reveals, the accumulator has no efficiently computable inverse, so
re-injecting the copied reveal produces an unrelated mix rather than cancelling
the victim's contribution, which an `xor` accumulator would have allowed.
```

**File:** specs/_features/eip8321/beacon-chain.md (L404-418)
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

**File:** specs/_features/eip8321/beacon-chain.md (L482-508)
```markdown
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
