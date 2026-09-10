## Finding

### Title
Uncapped `builder_pending_withdrawals` amount lets the execution layer mint Gwei beyond the beacon-state deduction - ([File: specs/gloas/beacon-chain.md])

### Summary
In the Gloas fork, `get_builder_withdrawals` builds `Withdrawal` objects using the raw, unclamped `amount` field stored in `state.builder_pending_withdrawals`, while `apply_withdrawals`/CL bookkeeping only ever decreases the builder's actual balance by `min(withdrawal.amount, builder_balance)`. Because the spec itself states the execution layer "mints the full committed amount regardless" of what the CL deducts, any queued builder withdrawal whose requested amount exceeds the builder's balance *at processing time* results in the execution layer crediting more Gwei to the builder's `fee_recipient` than was ever removed from the beacon state.

### Finding Description
`get_builder_withdrawals` copies the pending withdrawal's `amount` verbatim into the committed `Withdrawal`: [1](#0-0) 

Contrast this with every other withdrawal-generating helper in the same file, which clamps the amount that actually goes into the `Withdrawal` object to the available balance before it is ever placed in the committed list:
- `get_pending_partial_withdrawals`: `withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)` [2](#0-1) 
- `get_builders_sweep_withdrawals`: `amount=builder.balance` (the full, current balance) [3](#0-2) 

Only `get_builder_withdrawals` fails to clamp; the capping is deferred to `apply_withdrawals`, which decreases the CL-tracked `builder.balance` by `min(withdrawal.amount, builder_balance)`: [4](#0-3) 

This is the same bug class as the AuraSpell/WAuraPool report: the "burn" step (here, the CL-side balance deduction) is silently capped/short-changed relative to what is actually paid out ("mint" on the EL side), and nothing reconciles the shortfall. The spec's own authors acknowledge the general danger of this asymmetry for `decrease_balance` saturation in the surrounding note: [5](#0-4) 

That note discusses saturation from epoch-boundary consolidation reducing a validator's balance between commit and deduction, but the *same* mechanism applies directly to `get_builder_withdrawals`: nothing prevents `builder.balance` from being reduced (e.g., by `settle_builder_payment` charging the builder for a bid it won) between the time a `BuilderPendingWithdrawal` is queued (with some `amount`) and the time it is processed by `get_expected_withdrawals`/`apply_withdrawals`. When that happens, `get_builder_withdrawals` still emits a `Withdrawal` carrying the full stale `amount`, which the execution layer mints in full to `fee_recipient`, while the beacon state only ever removes `min(amount, current_balance)` Gwei from `builder.balance`.

The project's own test suite documents (rather than fixes) this exact discrepancy: [6](#0-5) 

### Impact Explanation
This breaks the "Gwei created... paid to a non-owner" invariant at Critical severity: ETH is minted on the execution layer without a corresponding, equal debit on the consensus layer. Any time a builder's balance is charged (via `settle_builder_payment`/bid settlement) after a withdrawal request for that builder has been queued but before it is swept, the resulting execution-layer withdrawal amount exceeds the beacon-state deduction, inflating total supply and paying the shortfall to whatever `fee_recipient` address was specified in the (attacker/builder-controlled) withdrawal request.

### Likelihood Explanation
No malicious peer or coalition is required. Any builder that (a) submits a withdrawal request for an amount it currently has, and then (b) has its balance reduced afterward (naturally, from having a subsequent bid it made win and settle, charging it) before the withdrawal is processed produces this state deterministically. This is a normal, expected sequence of builder participation, not an edge case requiring privileged access.

### Recommendation
Clamp the amount used in the `Withdrawal` object created by `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` at the time the withdrawal is generated (mirroring how `get_pending_partial_withdrawals` and `get_builders_sweep_withdrawals` compute their amounts), so the committed EL-minted amount can never exceed the actual CL-side deduction.

### Proof of Concept
1. Builder `B` has `balance = 10 ETH` and submits a builder withdrawal request for `10 ETH`, creating `BuilderPendingWithdrawal(builder_index=B, amount=10 ETH, fee_recipient=X)`.
2. Before this withdrawal is swept, `B` wins a bid and `settle_builder_payment` charges `B`'s balance down to `1 ETH` (per `apply_parent_execution_payload`) [7](#0-6) .
3. On the next call to `get_expected_withdrawals` → `get_builder_withdrawals`, the committed `Withdrawal` still carries `amount = 10 ETH` (unclamped) [8](#0-7) .
4. `apply_withdrawals` only decreases `B.balance` by `min(10 ETH, 1 ETH) = 1 ETH` [9](#0-8) , leaving `B.balance = 0`.
5. The execution layer, per the spec's own stated semantics ("the execution layer mints the full committed amount regardless"), credits address `X` with the full `10 ETH` even though only `1 ETH` was ever debited from the beacon state — a net 9 ETH created out of nothing.

### Citations

**File:** specs/gloas/beacon-chain.md (L1754-1761)
```markdown
    # Settle the builder payment
    if parent_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_epoch == get_previous_epoch(state):
        payment_index = parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_bid.value > 0:
```

**File:** specs/gloas/beacon-chain.md (L1815-1830)
```markdown
    for withdrawal in state.builder_pending_withdrawals:
        all_withdrawals = list(prior_withdrawals) + withdrawals
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if has_reached_limit:
            break

        builder_index = withdrawal.builder_index
        withdrawals.append(
            Withdrawal(
                index=withdrawal_index,
                validator_index=convert_builder_index_to_validator_index(builder_index),
                address=withdrawal.fee_recipient,
                amount=withdrawal.amount,
            )
        )
        withdrawal_index += 1
```

**File:** specs/gloas/beacon-chain.md (L1858-1867)
```markdown
        builder = state.builders[builder_index]
        if builder.withdrawable_epoch <= epoch and builder.balance > 0:
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=convert_builder_index_to_validator_index(builder_index),
                    address=builder.execution_address,
                    amount=builder.balance,
                )
            )
```

**File:** specs/gloas/beacon-chain.md (L1923-1932)
```markdown
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        # [Modified in Gloas:EIP7732]
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
```
```

**File:** specs/gloas/beacon-chain.md (L1977-1990)
```markdown
*Note*: Unlike deposits (which are applied at the child's slot via
`apply_parent_execution_payload`), withdrawal balance deductions are applied
immediately via `apply_withdrawals`. Deferring the deduction to the child's slot
would break the total supply invariant: state transitions between the commitment
slot and the deduction slot (e.g., `process_pending_consolidations` at an epoch
boundary) can reduce a validator's balance below the committed withdrawal
amount, causing `decrease_balance` to saturate at zero. Since the execution
layer mints the full committed amount regardless, any CL-side saturation creates
a net supply inflation. As a consequence, `state.balances` reflects the
withdrawal deduction before the corresponding execution payload is confirmed,
creating a transient asymmetry with the EL state at `state.latest_block_hash`.
Off-chain consumers that require CL/EL balance consistency can reconstruct
pre-deduction balances by adding back `state.payload_expected_withdrawals`.

```

**File:** specs/electra/beacon-chain.md (L1384-1393)
```markdown
        if is_eligible_for_partial_withdrawals(validator, balance):
            withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=validator_index,
                    address=ExecutionAddress(validator.withdrawal_credentials[12:]),
                    amount=withdrawal_amount,
                )
            )
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L116-156)
```python
def test_builder_withdrawal_insufficient_balance(spec, state):
    """
    Test builder withdrawal with insufficient balance.

    Input State Configured:
        - state.builders[0]: Builder exists with only 1 ETH balance
        - builder_pending_withdrawals: Contains 1 entry requesting 5 ETH
        - builders[0].balance: 1 ETH (insufficient for requested 5 ETH)

    Output State Verified:
        - payload_expected_withdrawals: Contains 1 withdrawal
        - withdrawal.amount: 5 ETH (requested amount)
        - builders[0].balance: 0 (deduction capped to available balance)
        - builder_pending_withdrawals: Reduced by 1 (processed even if capped)
        - next_withdrawal_index: Incremented by 1
    """
    builder_index = 0
    withdrawal_amount = spec.Gwei(5_000_000_000)
    available_balance = spec.Gwei(1_000_000_000)

    prepare_process_withdrawals(
        spec,
        state,
        builder_indices=[builder_index],
        builder_withdrawal_amounts={builder_index: withdrawal_amount},
        builder_balances={builder_index: available_balance},
    )

    pre_state = state.copy()
    yield from run_gloas_withdrawals_processing(spec, state)

    assert_process_withdrawals(
        spec,
        state,
        pre_state,
        withdrawal_count=1,
        builder_balances={builder_index: 0},
        builder_pending_delta=-1,
        withdrawal_index_delta=1,
        withdrawal_amounts_builders={builder_index: withdrawal_amount},
    )
```
