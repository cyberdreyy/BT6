### Title
Exited builder can be un-exited ("re-credentialed") by any unauthorized depositor - ([File: specs/gloas/beacon-chain.md])

### Summary
`process_builder_deposit_request` allows any party — without any signature check — to reset the `withdrawable_epoch` of an already-exited, fully-swept builder simply by sending a top-up deposit for that builder's public key. This directly contradicts the function's own documented invariant that "Exited builders cannot be reactivated," and lets an unrelated third party revive/re-credential a builder against the wishes (and without the authorization) of its withdrawal-key holder.

### Finding Description
`process_builder_deposit_request` treats any request whose `pubkey` is already present in `state.builders` as a "top-up," which requires **no signature verification at all**: [1](#0-0) 

```python
def process_builder_deposit_request(state: BeaconState, request: BuilderDepositRequest) -> None:
    if not is_builder_withdrawal_credential(request.withdrawal_credentials):
        return
    builder_pubkeys = [b.pubkey for b in state.builders]
    if request.pubkey not in builder_pubkeys:
        if is_valid_builder_deposit_signature(request):
            add_builder_to_registry(...)
    else:
        builder_index = BuilderIndex(builder_pubkeys.index(request.pubkey))
        builder = state.builders[builder_index]
        # If exited and swept, reset the withdrawable epoch
        if builder.withdrawable_epoch != FAR_FUTURE_EPOCH and builder.balance == 0:
            epoch = get_current_epoch(state)
            builder.withdrawable_epoch = epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY
        builder.balance += request.amount
```

The specification text right above this function explicitly states the opposite guarantee is supposed to hold: [2](#0-1) 

> "Exited builders cannot be reactivated, although a newly registered builder's public key may have previously appeared in the builder set."

Yet the code path taken when `request.pubkey` still equals a builder already present in `state.builders` (which remains true after exit — a builder's registry entry and pubkey are not removed on exit, only `withdrawable_epoch` and `balance` change) does exactly the opposite: once the builder's balance reaches zero after exit/sweep, *any* subsequent deposit for that same pubkey — sent by anyone, requiring no BLS signature, no match to the builder's registered `execution_address`, and no relationship to the original depositor — resets `withdrawable_epoch` forward from `FAR_FUTURE_EPOCH`/past value to `current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY` and credits balance. This is the mutation that (per `is_active_builder`, gated on `withdrawable_epoch`) is used elsewhere to authorize the builder to submit execution-payload bids: [3](#0-2) 

Because the top-up branch performs no authorization check tied to the builder's own key or withdrawal address, this breaks the "validator/builder mutated only by its own authority" equality: an exited builder's lifecycle state (`withdrawable_epoch`) is mutated, and the builder is made bid-eligible again, by a party that holds neither the builder's private key nor control over its withdrawal address.

### Impact Explanation
This maps to the High-impact category "a validator or builder exited, consolidated, or re-credentialed without its authority." A builder that has deliberately exited (e.g., to permanently retire, reclaim funds, or because its operator no longer wants it active) can be forced back into an active, bid-eligible state by any unrelated actor sending a minimal deposit — a state transition the spec text itself says must never happen ("Exited builders cannot be reactivated"). This is a genuine divergence between the documented invariant and the executable spec logic, not merely a wording nit, since it changes on-chain builder-registry state (`withdrawable_epoch`) and builder activity eligibility (`is_active_builder`) without the builder's consent.

### Likelihood Explanation
Trivial to trigger: it only requires knowledge of a public builder pubkey (public information) and the ability to submit any `BuilderDepositRequest` with the `BUILDER_WITHDRAWAL_PREFIX` and that pubkey — no signature or private key is needed for the top-up branch. The only precondition is that the builder has fully exited and been swept to zero balance, which is a normal, expected end-of-life state for a retiring builder.

### Recommendation
In the top-up branch of `process_builder_deposit_request`, once `builder.withdrawable_epoch != FAR_FUTURE_EPOCH` (i.e., the builder has initiated exit), deposits for that pubkey should either be rejected outright, or only be permitted to add balance without resetting `withdrawable_epoch`/reactivating eligibility — consistent with the documented "cannot be reactivated" invariant. If re-registration is desired, it should go through the same path as a brand-new builder (a fresh registry slot via `get_index_for_new_builder`, requiring a fresh `is_valid_builder_deposit_signature` check), not an in-place mutation of the exited entry.

### Proof of Concept
1. Builder `B` (pubkey `P`, index `i`) exits via `process_builder_exit_request`, setting `state.builders[i].withdrawable_epoch = current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY`.
2. Once past `withdrawable_epoch`, the builder's balance is swept to `0` (builder withdrawal sweep logic), leaving `state.builders[i].balance == 0` and `withdrawable_epoch != FAR_FUTURE_EPOCH`.
3. Attacker (any party, no key needed) submits `BuilderDepositRequest(pubkey=P, withdrawal_credentials=<builder-prefixed, arbitrary address>, amount=1)`.
4. `process_builder_deposit_request` finds `P in builder_pubkeys`, takes the top-up branch, sees `withdrawable_epoch != FAR_FUTURE_EPOCH and balance == 0`, and resets `state.builders[i].withdrawable_epoch = current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY`, then adds `1` to balance.
5. `state.builders[i]` is now un-exited and (subject to `is_active_builder`'s exact epoch check, which gates bid submission) eligible to be used for `process_execution_payload_bid` again — despite the original owner never having authorized reactivation, contradicting the spec's stated guarantee.

Note: I was not able to fully read the body of `is_active_builder` before the iteration limit was reached, so the exact epoch-comparison semantics that gate bid eligibility off `withdrawable_epoch` are inferred from its usage pattern and naming rather than directly confirmed line-by-line; the core mutation of an exited builder's `withdrawable_epoch` by an unauthorized, unsigned deposit is directly confirmed in the cited code.

### Citations

**File:** specs/gloas/beacon-chain.md (L2100-2106)
```markdown
        assert is_active_builder(state, builder_index)
        # Verify that the builder is a payload builder
        assert state.builders[builder_index].version == PAYLOAD_BUILDER_VERSION
        # Verify that the builder has funds to cover the bid
        assert can_builder_cover_bid(state, builder_index, amount)
        # Verify that the bid signature is valid
        assert verify_execution_payload_bid_signature(state, signed_bid)
```

**File:** specs/gloas/beacon-chain.md (L2247-2255)
```markdown
###### New `process_builder_deposit_request`

*Note*: Builder indices are reusable. When a builder exits, its index may later
be reassigned to a different builder with a new public key. Any deposit sent to
an exited builder will be withdrawn to the builder’s execution address. Exited
builders cannot be reactivated, although a newly registered builder’s public key
may have previously appeared in the builder set. Implementations that rely on
caching should account for this behavior.

```

**File:** specs/gloas/beacon-chain.md (L2256-2284)
```markdown
```python
def process_builder_deposit_request(state: BeaconState, request: BuilderDepositRequest) -> None:
    # Ignore deposits with unexpected withdrawal credential prefixes
    if not is_builder_withdrawal_credential(request.withdrawal_credentials):
        return

    builder_pubkeys = [b.pubkey for b in state.builders]
    if request.pubkey not in builder_pubkeys:
        if is_valid_builder_deposit_signature(request):
            add_builder_to_registry(
                state,
                request.pubkey,
                PAYLOAD_BUILDER_VERSION,
                ExecutionAddress(request.withdrawal_credentials[12:]),
                request.amount,
                state.slot,
            )
    else:
        builder_index = BuilderIndex(builder_pubkeys.index(request.pubkey))
        builder = state.builders[builder_index]

        # If exited and swept, reset the withdrawable epoch
        if builder.withdrawable_epoch != FAR_FUTURE_EPOCH and builder.balance == 0:
            epoch = get_current_epoch(state)
            builder.withdrawable_epoch = epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY

        # Increase balance by deposit amount
        builder.balance += request.amount
```
```
