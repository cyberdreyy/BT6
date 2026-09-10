Confirmed: `get_builder_withdrawals` builds the committed `Withdrawal.amount` from the raw pending-withdrawal request `withdrawal.amount` without capping it to the builder's actual balance [1](#0-0) , while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance` [2](#0-1) . This is exactly the analog of the report's bug class: the accounting-commitment step (the `Withdrawal` record that the EL is required to honor) and the actual-value-moved step (the balance debit) can disagree, so value can be paid out that isn't actually deducted from its source.

### Title
Uncapped builder-requested withdrawal amount lets the EL mint Gwei not backed by the builder's deducted balance - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies the raw, unclamped `amount` field from `state.builder_pending_withdrawals` into the `Withdrawal.amount` of the committed `payload_expected_withdrawals` list, whereas `apply_withdrawals` deducts only `min(withdrawal.amount, builder_balance)` from the builder's CL-tracked balance. When a builder's pending withdrawal request exceeds its current balance, the committed withdrawal payload instructs the execution layer to pay out the full requested amount to `fee_recipient`, while the consensus layer only removes the (smaller) available balance from the builder's stake.

### Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md) builds each `Withdrawal` for `state.builder_pending_withdrawals` using `amount=withdrawal.amount` directly, with no comparison to `state.builders[builder_index].balance` [3](#0-2) . This `Withdrawal` list becomes `state.payload_expected_withdrawals`, which the execution layer is contractually bound to execute as literal ETH payments (per the EIP-4895/7732 withdrawal-crediting model used throughout Capella/Electra/Gloas withdrawals) — the EL credits `address` with the full `amount` unconditionally, since withdrawals (unlike ERC-20 `transfer()`) cannot "fail"; they are guaranteed, unconditional mints from the CL's commitment.

Meanwhile, `apply_withdrawals` (Modified in Gloas) only decreases the builder's CL-tracked balance by the *capped* amount:
```python
builder_balance = state.builders[builder_index].balance
state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [2](#0-1) 

So if `builder_pending_withdrawals[i].amount > state.builders[builder_index].balance` at processing time, the committed `Withdrawal.amount` (paid by EL, in full) exceeds the amount actually deducted from the builder's stake on the CL side. The equality that should hold — "Gwei paid out to `fee_recipient` == Gwei removed from the builder's balance" — is broken: more is paid than was ever backed by stake.

This is confirmed by the project's own test `test_builder_withdrawal_insufficient_balance`, which explicitly asserts this behavior:
```
withdrawal.amount: 5 ETH (requested amount)
builders[0].balance: 0 (deduction capped to available balance)
``` [4](#0-3)  The test documents the mismatch as intended behavior rather than treating it as a bug, but the invariant checker in the shared test helper only verifies that the *builder balance* decreases by `min(total_amount, pre_balance)` [5](#0-4)  — it never checks that the committed `Withdrawal.amount` paid to `fee_recipient` is bounded by that same balance, so the excess payment is invisible to the invariant suite.

Contrast this with the validator-side full/partial withdrawal path, where `Withdrawal.amount` is derived directly from `balance` (e.g. `amount=balance` or `amount=min(balance - MIN_ACTIVATION_BALANCE, ...)`) [6](#0-5)  — the committed amount is always bounded by actual balance for validators. Builders are the only case where the committed payment amount is decoupled from the balance-deduction cap.

### Impact Explanation
A builder (or anyone able to enqueue a `BuilderPendingWithdrawal`, e.g. via builder payment/withdrawal-request mechanisms feeding `state.builder_pending_withdrawals`) whose balance has dropped below a previously-requested withdrawal amount can cause the protocol to commit an execution-layer payment larger than the balance actually removed from the CL. This is Gwei paid to `fee_recipient` that was never backed by a corresponding CL balance decrease — a direct violation of the "Gwei created/paid without authority" invariant, and a real (if bounded per-occurrence) supply-inflation / fund-misdirection bug reachable purely through spec-following state transitions, with no malicious peer or client bug required.

### Likelihood Explanation
Reaching the insufficient-balance state simply requires a builder to have multiple pending withdrawals queued, or other balance-reducing events (fee deductions, slashing-like penalties on the builder registry, etc.) between when the withdrawal was queued and when it is processed, reducing `builder.balance` below the previously queued `amount`. Given `state.builder_pending_withdrawals` can accumulate up to `BUILDER_PENDING_WITHDRAWALS_LIMIT` (2^20) entries [7](#0-6)  and the mismatch is explicitly exercised (and tolerated) in the project's own test suite, this is a straightforward, deterministically reachable path.

### Recommendation
Cap the `Withdrawal.amount` produced in `get_builder_withdrawals` to the builder's current balance, mirroring what `apply_withdrawals` actually deducts:
```python
amount=min(withdrawal.amount, state.builders[builder_index].balance)
```
so the committed payment the EL is obligated to execute can never exceed what the CL removes from the builder's tracked balance. Alternatively, make `apply_withdrawals` assert `withdrawal.amount <= builder_balance` (rejecting/erroring rather than silently capping), forcing `get_builder_withdrawals` (or the request-enqueuing logic) to guarantee the invariant holds before commitment, analogous to using `safeTransfer` instead of a return-value-ignoring `transfer` in the original report — i.e., failing loudly rather than allowing amount and deduction to silently diverge.

### Proof of Concept
1. Register builder `B` with balance `1 ETH`.
2. Enqueue `BuilderPendingWithdrawal(builder_index=B, fee_recipient=X, amount=5 ETH)` into `state.builder_pending_withdrawals`.
3. Call `get_expected_withdrawals(state)` → `get_builder_withdrawals` emits `Withdrawal(validator_index=convert(B), address=X, amount=5 ETH)` (uncapped) [3](#0-2) .
4. `process_withdrawals` sets `state.payload_expected_withdrawals` to include this `5 ETH` withdrawal, which the execution layer is required to honor by crediting `X` with `5 ETH`.
5. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)` → builder balance becomes `0`, i.e., only `1 ETH` was ever removed from CL-tracked builder stake [8](#0-7) .
6. Net effect: `X` receives `5 ETH` on the EL, but only `1 ETH` was deducted anywhere in the CL state — `4 ETH` of Gwei is paid out with no corresponding CL-side removal, exactly matching the pattern in `tests/.../test_process_withdrawals.py::test_builder_withdrawal_insufficient_balance`, which asserts `withdrawal.amount == 5 ETH` (requested) alongside `builders[0].balance == 0` (capped deduction) [9](#0-8) .

### Citations

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

**File:** tests/core/pyspec/eth_consensus_specs/test/helpers/withdrawals.py (L721-729)
```python
    # Check builder balance decreases
    for builder_index, total_amount in builder_withdrawals.items():
        pre_balance = pre_state.builders[builder_index].balance
        post_balance = state.builders[builder_index].balance
        # Builder withdrawals cap at available balance (spec uses min())
        expected_deduction = min(total_amount, pre_balance)
        assert post_balance == pre_balance - expected_deduction, (
            f"Builder {builder_index} balance must decrease by withdrawal amount"
        )
```

**File:** specs/electra/beacon-chain.md (L1381-1394)
```markdown
        validator_index = withdrawal.validator_index
        validator = state.validators[validator_index]
        balance = get_balance_after_withdrawals(state, validator_index, all_withdrawals)
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
            withdrawal_index += 1
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L31-31)
```markdown
| `state.builder_pending_withdrawals`                      | `List[BuilderPendingWithdrawal, BUILDER_PENDING_WITHDRAWALS_LIMIT]` | Length in [0, 2^20]. Processed first. Max `MAX_WITHDRAWALS_PER_PAYLOAD - 1` can produce withdrawals.                                                                 | Iterate for builder pending withdrawals |
```
