## Title
`get_builder_withdrawals` commits the full unclamped pending amount to the withdrawal record, while `apply_withdrawals` only deducts `min(withdrawal.amount, builder_balance)` from the builder — (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_builder_withdrawals()` copies `withdrawal.amount` from `state.builder_pending_withdrawals` verbatim into the `Withdrawal.amount` field that becomes part of `state.payload_expected_withdrawals` — the value the execution layer is committed to mint to `withdrawal.fee_recipient`. The actual balance deduction, performed later in `apply_withdrawals`, is capped with `min(withdrawal.amount, builder_balance)`. If a builder's balance has dropped below the pending withdrawal amount (e.g., due to intervening bid payments/settlements or another withdrawal consuming the balance first), the withdrawal committed to the EL still carries the full, uncapped `amount`, but only the smaller, capped amount is actually removed from the builder's ledger balance in `state.builders[...].balance`.

### Finding Description
Compare the two sides of the same value:

- Construction (commitment side), `get_builder_withdrawals`, [1](#0-0) , copies `withdrawal.amount` unmodified from `state.builder_pending_withdrawals` into the outgoing `Withdrawal.amount`, with no comparison to the builder's current balance.
- Application (deduction side), `apply_withdrawals`, [2](#0-1) , deducts only `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`.

This is precisely the shape of the Halborn/Beanstalk bug: a value returned/derived from an authoritative source (`receiveToken()`'s actual transferred amount ↔ here, the builder's actually-available balance) is silently clamped on one side of an operation, but the *unclamped, requested* value is what gets used for the externally-visible payment commitment (the pool swap ↔ here, the `Withdrawal.amount` baked into `payload_expected_withdrawals`, which the execution layer is required to honor for `fee_recipient`). The test suite explicitly documents this asymmetry as intended behavior: [3](#0-2) 

which states: `withdrawal.amount`: 5 ETH (requested amount) while `builders[0].balance`: 0 (deduction capped to available balance) — i.e., the CL only ever debits 1 ETH from the builder's own balance sheet, but the withdrawal record that the EL must pay out commits to minting 5 ETH to the builder's `fee_recipient`.

Because `state.balances`/`state.builders[*].balance` is the only CL-side bookkeping of "backing" for Gwei that the EL will mint on withdrawal, any withdrawal whose committed `amount` exceeds what was actually deducted from the paying party's balance breaks the equality: *Gwei paid out to fee_recipient == Gwei actually removed from the payer's balance*. This is a "Gwei created" class break — the excess (`amount − min(amount, balance)`) is money the EL is contractually committed to mint (per `process_withdrawals`'s deterministic withdrawal semantics, honored by the EL) but that was never actually debited from anyone's tracked CL balance.

### Impact Explanation
This falls under Critical: "Gwei created, destroyed or paid to a non-owner." The scenario documented by the spec's own tests (builder balance = 1 ETH, pending withdrawal amount = 5 ETH) shows that the recorded withdrawal amount (5 ETH) is unconditionally greater than what is deducted (1 ETH) from the builder's ledger. Since `payload_expected_withdrawals` is the exact list the execution-layer payload must match (enforced via `assert list(payload.withdrawals) == expected.withdrawals` in earlier forks and via the deterministic honoring requirement in Gloas), the EL is required to mint the full committed 5 ETH to `fee_recipient`, i.e. 4 ETH is created out of nothing relative to CL-tracked builder balances.

### Likelihood Explanation
This is not attacker-controlled in the sense of an external party forging the discrepancy directly — it arises whenever a `BuilderPendingWithdrawal.amount` (created earlier, e.g. from a bid's `value` at `process_execution_payload_bid`, [4](#0-3) ) is enqueued and the builder's `balance` subsequently decreases below that queued amount before the withdrawal is processed (e.g. via another queued withdrawal being applied first, or `settle_builder_payment` debiting the balance in the interim, or repeated pending payments/withdrawals against the same shrinking balance). Given the multi-slot latency between bid acceptance (queuing the pending payment/withdrawal) and withdrawal sweep processing, and the explicit `min()`-clamp-but-unclamped-amount pattern shown by the spec's own regression test, this is readily reachable through ordinary protocol operation without requiring a malicious peer or client bug — it is a spec-level design/implementation gap in how the withdrawal amount is derived versus how the balance deduction is capped.

### Recommendation
`get_builder_withdrawals` should clamp the `Withdrawal.amount` to `min(withdrawal.amount, state.builders[builder_index].balance)` at construction time (mirroring the eth1/CL sweep logic in `get_validators_sweep_withdrawals`, which withdraws `balance` or `balance - MAX_EFFECTIVE_BALANCE`, never more than what actually exists), so that the value committed into `payload_expected_withdrawals` (and thus what the EL is obligated to mint) is always equal to the amount actually deducted in `apply_withdrawals`. Alternatively, `apply_withdrawals` should assert `withdrawal.amount <= builder_balance` rather than silently capping the deduction while leaving the committed amount inflated.

### Proof of Concept
1. Builder `B` submits/accepts a winning bid with `bid.value = 5 ETH` while `B.balance = 6 ETH` (via `process_execution_payload_bid`), creating a `BuilderPendingPayment`/eventual `BuilderPendingWithdrawal { builder_index: B, amount: 5 ETH, fee_recipient: X }`.
2. Before this withdrawal is swept, `B`'s balance is reduced to 1 ETH through some other legitimate mechanism (e.g. another pending withdrawal for `B` processed first in the same or an earlier sweep, or additional slashing/settlement activity reducing `state.builders[B].balance`).
3. `get_builder_withdrawals` runs and emits `Withdrawal { validator_index: convert(B), address: X, amount: 5 ETH }` unconditionally — see [5](#0-4) .
4. This withdrawal enters `state.payload_expected_withdrawals`, which the execution layer is required to honor and mint 5 ETH to `X`.
5. `apply_withdrawals` then executes `state.builders[B].balance -= min(5 ETH, 1 ETH)`, leaving `B.balance = 0` — see [2](#0-1) .
6. Net effect: 4 ETH was minted to `X` by the EL that was never debited from any CL-tracked balance — this exact behavior (amount=requested vs. balance deduction=capped) is confirmed as the specified/tested outcome in [3](#0-2) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1833)
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
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```

**File:** specs/gloas/beacon-chain.md (L1920-1932)
```markdown
##### Modified `apply_withdrawals`

```python
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

**File:** specs/gloas/beacon-chain.md (L2124-2137)
```markdown
    # Record the pending payment if there is some payment
    if amount > 0:
        pending_payment = BuilderPendingPayment(
            weight=Gwei(0),
            withdrawal=BuilderPendingWithdrawal(
                fee_recipient=bid.fee_recipient,
                amount=amount,
                builder_index=builder_index,
            ),
            proposer_index=get_beacon_proposer_index(state),
        )
        state.builder_pending_payments[SLOTS_PER_EPOCH + bid.slot % SLOTS_PER_EPOCH] = (
            pending_payment
        )
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L114-156)
```python
@with_gloas_and_later
@spec_state_test
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
