### Title
Unauthorized builder deposit top-up resurrects an exited builder's `withdrawable_epoch` without the builder's authority - (File: specs/gloas/beacon-chain.md)

### Summary
`process_builder_deposit_request` allows *any* execution-layer caller to top up an **existing** builder's balance without any signature check, and — if that builder has already exited and been fully swept (`balance == 0`) — this permissionless top-up resets `builder.withdrawable_epoch` forward to `current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY`. This is functionally identical to the `lockOnBehalf` bug: an unauthenticated third party, spending an arbitrary (non-minimum-checked at this layer) amount, can extend another party's exit/withdrawal timer, contradicting the explicit spec invariant that "exited builders cannot be reactivated."

### Finding Description
`process_builder_deposit_request` branches on whether `request.pubkey` already belongs to a registered builder: [1](#0-0) 

For a fresh pubkey it requires `is_valid_builder_deposit_signature`, but for an **existing** pubkey it goes into the `else` branch (top-up), which performs **no signature check whatsoever** — confirmed explicitly by the test suite: [2](#0-1) 

Crucially, within that unauthenticated top-up branch, if the builder had already exited and been swept to zero balance, the code silently resets `withdrawable_epoch`:

```
# If exited and swept, reset the withdrawable epoch
if builder.withdrawable_epoch != FAR_FUTURE_EPOCH and builder.balance == 0:
    epoch = get_current_epoch(state)
    builder.withdrawable_epoch = epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY

# Increase balance by deposit amount
builder.balance += request.amount
```

The spec's own builder documentation states the intended invariant is the opposite of what the code does: [3](#0-2) 

> "Exited builders cannot be reactivated, although a newly registered builder's public key may have previously appeared in the builder set."

Yet the code path shown above does exactly that: it takes an exited, fully-swept builder (whose registry slot is meant to become reusable, per `get_index_for_new_builder`, once `withdrawable_epoch <= current_epoch and balance == 0`) and un-exits it by advancing its `withdrawable_epoch` and giving it a nonzero balance again — all triggerable by anyone, with no signature over the original builder's pubkey, and with no enforced minimum deposit amount in this state-transition function (the `MIN_DEPOSIT_AMOUNT` floor mentioned in `specs/gloas/builder.md` is only a validator-facing recommendation for the EL request, not a check inside `process_builder_deposit_request`).

Also confirmed by test that other top-up request fields (including withdrawal credentials) are ignored and the pre-existing registration is preserved except for balance/epoch: [4](#0-3) 

Before/after on a concrete state:
- Before: `builder.withdrawable_epoch = E ≤ current_epoch`, `builder.balance = 0` (slot is reusable by `get_index_for_new_builder`).
- Attacker sends an unsigned/garbage-signature `BuilderDepositRequest` for that pubkey with a minimal amount.
- After: `builder.withdrawable_epoch = current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY`, `builder.balance = amount > 0`.

This breaks the equality that a builder's exit/withdrawal state can only be mutated by the builder's own authority (signature) — it is instead mutated by an arbitrary unauthenticated party.

### Impact Explanation
This matches the High-impact category "a validator or builder exited, consolidated, or re-credentialed without its authority" (here, inverted: un-exited/reactivated without authority). Concretely:
- The exited builder's registry slot, which the protocol intends to free for reuse by a genuinely new builder, is kept occupied and its "exit" is effectively undone without any action or consent from the original builder.
- The builder's `withdrawable_epoch` — a protocol-level timer analogous to the `lockedToken.unlockTime` in the referenced report — is extended by a party who does not hold the builder's private key, exactly mirroring the `lockOnBehalf` griefing pattern (arbitrary caller extends someone else's unlock/exit timer with no minimum-amount gate).

### Likelihood Explanation
Likelihood is high given the mechanism: any block proposer/user able to submit an `ExecutionRequests.builder_deposits` entry (a permissionless EL request type) can target any previously-exited, swept builder pubkey — which is public information (all builder pubkeys are inspectable in state) — with no signature and an arbitrary amount, at any time after that builder is swept.

### Recommendation
Require the same signature verification (`is_valid_builder_deposit_signature`) for top-ups targeting builders whose `withdrawable_epoch != FAR_FUTURE_EPOCH` (i.e., already exited), or, in line with the documented invariant, disallow topping up an exited-and-swept builder entirely and instead treat such deposits as creating a *new* builder registration (subject to normal slot-reuse rules and signature checks), so that an exited builder's identity cannot be resurrected by an unauthenticated deposit.

### Proof of Concept
Conceptual reproduction using the existing test harness (`tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_builder_deposit_request.py`):
1. Start from `test_process_builder_deposit_request__exited_builder_top_up_zero_balance` setup: builder `0` has `withdrawable_epoch = current_epoch - 1` and `balance = 0` (exited and swept). [5](#0-4) 
2. Craft a `BuilderDepositRequest` for `state.builders[0].pubkey` with an invalid/garbage signature (as in `test_process_builder_deposit_request__top_up_invalid_sig`), using any account, not the builder's own key.
3. Call `process_builder_deposit_request(state, request)`.
4. Observe `state.builders[0].withdrawable_epoch` is reset to `current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY` and `state.builders[0].balance` becomes nonzero — despite the caller never proving control of the builder's pubkey, and despite the spec's stated invariant that exited builders cannot be reactivated.

### Citations

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

**File:** specs/gloas/beacon-chain.md (L2257-2284)
```markdown
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_builder_deposit_request.py (L616-631)
```python
@with_gloas_and_later
@spec_state_test
@always_bls
def test_process_builder_deposit_request__top_up_invalid_sig(spec, state):
    """Test that top-up deposit with invalid signature still succeeds for existing builders."""
    amount = spec.MIN_DEPOSIT_AMOUNT
    pubkey = state.builders[0].pubkey
    # Don't sign the deposit
    builder_deposit_request = prepare_builder_deposit_request(
        spec, state, amount, pubkey=pubkey, signed=False
    )

    # Top-ups don't require signature verification for existing builders
    yield from run_builder_deposit_processing(
        spec, state, builder_deposit_request, is_new_builder=False
    )
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_builder_deposit_request.py (L835-883)
```python
@with_gloas_and_later
@spec_state_test
def test_process_builder_deposit_request__exited_builder_top_up_zero_balance(spec, state):
    """
    Test top-up to a fully-swept exited builder resets its withdrawable epoch.

    Input State Configured:
        - Existing builder at index 0 that has exited (withdrawable_epoch in the
          past) and has been fully swept (balance == 0)

    Output State Verified:
        - Builder balance increased (top-up)
        - withdrawable_epoch reset to current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY
    """
    builder_pubkey = state.builders[0].pubkey
    amount = spec.MIN_DEPOSIT_AMOUNT
    pre_builder_count = len(state.builders)

    # Advance an epoch and mark builder 0 as exited (withdrawable_epoch in the
    # past) and fully swept (balance == 0)
    deposit_request = prepare_process_builder_deposit_request(
        spec,
        state,
        pubkey=builder_pubkey,
        amount=amount,
        signed=True,
        advance_epochs=1,
        builder_modifications={0: {"withdrawable_epoch": "current_epoch-1", "balance": 0}},
    )
    pre_state = state.copy()
    expected_withdrawable_epoch = (
        spec.get_current_epoch(state) + spec.config.MIN_BUILDER_WITHDRAWABILITY_DELAY
    )
    # Sanity check: the exited epoch differs from the expected reset value
    assert pre_state.builders[0].withdrawable_epoch != expected_withdrawable_epoch
    assert pre_state.builders[0].balance == 0

    yield from run_builder_deposit_request_processing(spec, state, deposit_request)

    assert_process_builder_deposit_request(
        spec,
        state,
        pre_state,
        builder_deposit_request=deposit_request,
        expected_builder_count=pre_builder_count,
        expected_builder_index=0,
        expected_builder_balance_delta=amount,
        expected_builder_withdrawable_epoch=expected_withdrawable_epoch,
    )
```
