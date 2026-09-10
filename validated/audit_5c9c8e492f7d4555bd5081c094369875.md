Confirmed. `get_builder_withdrawals` at [1](#0-0)  creates a `Withdrawal` whose `amount` is copied verbatim from `state.builder_pending_withdrawals[i].amount` — the originally committed/promised payment value, with no clamping to the builder's current balance. That withdrawal (uncapped `amount`, destined for `withdrawal.address`) is placed into `state.payload_expected_withdrawals`, which per spec note the execution layer "is required to honor" exactly. Meanwhile `apply_withdrawals` only debits the builder by `min(withdrawal.amount, builder_balance)`: [2](#0-1) 

### Title
Builder withdrawal amount sent to the execution layer is not capped to the builder's actual balance, creating unbacked Gwei - (File: specs/gloas/beacon-chain.md)

### Summary
When a builder's pending payment/withdrawal amount exceeds the builder's current CL balance, `get_builder_withdrawals` still emits a `Withdrawal` with the full, uncapped `amount` into `state.payload_expected_withdrawals` (the EL-facing withdrawals list), while `apply_withdrawals` only decreases the builder's CL balance by `min(withdrawal.amount, builder_balance)`. The execution layer credits `withdrawal.address` with the full committed `amount` (as it must, since payload withdrawals are a binding commitment), but the beacon state only ever removed the smaller, capped amount from any account. The difference is Gwei that appears on the EL side without ever being subtracted from CL state — new value created out of nothing.

### Finding Description
`state.builder_pending_withdrawals` entries carry an `amount` that was fixed when the payment/pending-withdrawal was recorded (e.g., the bid `value` accepted in `process_execution_payload_bid`, later moved to `builder_pending_withdrawals` by `settle_builder_payment`/`process_builder_pending_payments`). `can_builder_cover_bid` is only checked once, at bid-acceptance time. Between acceptance and the eventual withdrawal sweep, the builder's balance can shrink (e.g., other withdrawals/payments settle first, multiple bids stack against the same balance, `process_withdrawals` processes several `builder_pending_withdrawals` entries in the same sweep against a balance too small to cover all of them — as explicitly exercised by `test_duplicate_builder_index_in_pending_withdrawals` and `test_builder_withdrawal_insufficient_balance`).

`get_builder_withdrawals` (specs/gloas/beacon-chain.md:1805-1834) builds the `Withdrawal.amount` directly from `withdrawal.amount` (the stored, uncapped pending amount) with no `min()` against `state.builders[builder_index].balance`. This uncapped value is what goes into `state.payload_expected_withdrawals`, which the execution layer is contractually bound to honor by paying `withdrawal.address` exactly `withdrawal.amount` gwei. `apply_withdrawals` (specs/gloas/beacon-chain.md:1923-1931), however, deducts only `min(withdrawal.amount, builder_balance)` from the builder's CL-tracked balance. The test suite's own invariant helper documents this cap explicitly: "Builder withdrawals cap at available balance (spec uses min())" (tests/core/pyspec/eth_consensus_specs/test/helpers/withdrawals.py:721-729), and `test_builder_withdrawal_insufficient_balance` explicitly asserts `withdrawal.amount` stays at the full requested 5 ETH while the balance decrease is capped to the available 1 ETH.

This breaks the fundamental equality that every Gwei paid out (on the EL side, per the withdrawal commitment) must correspond to a Gwei removed from CL state. Here, `withdrawal.amount − min(withdrawal.amount, balance)` gwei is paid to `fee_recipient`/`execution_address` on the execution layer with no corresponding decrease anywhere in the beacon state — inflating total EL-side balance beyond what CL accounting reflects.

### Impact Explanation
This is a Critical-class issue: Gwei is created and paid to a party (the builder's fee recipient / execution address) that the protocol never actually backed with a corresponding balance decrease. It is a fork-consensus-relevant invariant (all spec-following nodes compute the same `get_expected_withdrawals`/`apply_withdrawals`, so all nodes agree on the same broken accounting), meaning it is not merely a client bug but a defect reachable purely from spec logic — the EL is required to honor the exact withdrawal amounts committed by the CL, so the excess payment is unavoidable once the underfunded state is reached.

### Likelihood Explanation
Reaching underfunded-builder-withdrawal state does not require any malicious coalition: a builder can legitimately accumulate multiple pending payments/withdrawals against its own balance across several blocks (each individually passing `can_builder_cover_bid` against the balance *at bid time*), and then have several of those pending withdrawals swept in the same `process_withdrawals` call once the balance has since been reduced (by the earlier withdrawals in the same sweep, or by a builder exit/other event). The spec's own test suite deliberately constructs and asserts this exact scenario (`test_builder_withdrawal_insufficient_balance`, `test_duplicate_builder_index_in_pending_withdrawals`), confirming it is a reachable, spec-following state rather than a theoretical edge case.

### Recommendation
Cap the `amount` field of the `Withdrawal` object itself (not just the CL-side balance deduction) to the builder's actual available balance at the time `get_builder_withdrawals`/`get_builders_sweep_withdrawals` is called, e.g. `amount=min(withdrawal.amount, running_available_balance)`, and track a running available-balance decrement across successive pending withdrawals processed within the same call so multiple withdrawals against the same builder in one sweep cannot jointly exceed the builder's balance. This ensures the amount instructed to the execution layer never exceeds what is actually deducted from CL state.

### Proof of Concept
1. Builder `b` submits bid A with `value = 10 ETH` when `state.builders[b].balance = 10 ETH`; `can_builder_cover_bid` passes. This becomes a `builder_pending_payments` entry, later moved into `state.builder_pending_withdrawals` via `settle_builder_payment` with `amount = 10 ETH`.
2. Before that withdrawal is swept, the builder submits and gets accepted another action that adds a second `builder_pending_withdrawals` entry (or an exit/other payment) for the same builder — e.g., two payments each independently checked at acceptance time but never re-validated jointly against the now-shared 10 ETH balance (mirrors `test_duplicate_builder_index_in_pending_withdrawals`, which builds 3× 1 ETH pending withdrawals against a balance of exactly `3 × amount + MIN_DEPOSIT_AMOUNT`, i.e., only just enough — reduce that balance slightly and the shortfall reproduces).
3. `process_withdrawals` calls `get_builder_withdrawals`, which emits `Withdrawal(amount=10 ETH, address=fee_recipient_A)` for the first entry unmodified into `payload_expected_withdrawals`.
4. `apply_withdrawals` runs: for this withdrawal, `builder_balance` may already be less than 10 ETH (e.g., 4 ETH) after an intervening deduction; `state.builders[b].balance -= min(10 ETH, 4 ETH)` deducts only 4 ETH.
5. `state.payload_expected_withdrawals` still lists an EL-binding withdrawal of 10 ETH to `fee_recipient_A`. The execution layer credits 10 ETH to that address. Only 4 ETH was ever removed from CL state — 6 ETH of Gwei was created and paid out with no corresponding source, confirmed directly by `test_builder_withdrawal_insufficient_balance`'s assertion that `withdrawal.amount == withdrawal_amount` (uncapped) while `builders[0].balance == 0` (capped deduction) [3](#0-2) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1829)
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
```

**File:** specs/gloas/beacon-chain.md (L1923-1931)
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
