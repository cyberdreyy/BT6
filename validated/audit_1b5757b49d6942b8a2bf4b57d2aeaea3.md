### Title
Builder pending withdrawals mint Gwei to `fee_recipient` beyond the builder's actual balance - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` in `specs/gloas/beacon-chain.md` places the *full requested* `BuilderPendingWithdrawal.amount` into the committed `Withdrawal` that goes into `state.payload_expected_withdrawals` (and thus into the execution payload that the EL must honor), while `apply_withdrawals` only deducts `min(withdrawal.amount, builder_balance)` from the builder's actual CL-tracked balance. When a builder's balance is insufficient to cover the requested withdrawal amount, the amount actually removed from `state.builders[*].balance` is silently capped, but the amount committed for EL payout is not reduced to match — exactly mirroring the FraxLend `liquidateClean` bug pattern where "leftover" is written off in one place but not reflected in the other, breaking the invariant that decrease-in-balance == amount-paid-out.

### Finding Description
`BuilderPendingWithdrawal.amount` is an unconstrained field (only capped when the payment itself is created via `process_builder_pending_payments`, but that path is bounded by builder balance at creation time — see below on when this can drift). In `get_builder_withdrawals`:

```python
withdrawals.append(
    Withdrawal(
        index=withdrawal_index,
        validator_index=convert_builder_index_to_validator_index(builder_index),
        address=withdrawal.fee_recipient,
        amount=withdrawal.amount,   # <-- full requested amount, NOT capped
    )
)
``` [1](#0-0) 

This `Withdrawal` (with the uncapped `amount`) becomes part of `state.payload_expected_withdrawals`, which the execution layer is required to honor (mint/credit `amount` to `address`) once the corresponding execution payload is applied. Separately, `apply_withdrawals` deducts only the capped amount from the CL-side accounting:

```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
``` [2](#0-1) 

This is confirmed by the repo's own spec-conformance tests, which explicitly document that the committed `Withdrawal.amount` stays at the full *requested* value while the actual balance deduction is capped:

```
Output State Verified:
    - payload_expected_withdrawals: Contains 1 withdrawal
    - withdrawal.amount: 5 ETH (requested amount)
    - builders[0].balance: 0 (deduction capped to available balance)
``` [3](#0-2) 

The equality that should hold — "Gwei paid out to a destination == Gwei removed from the source's balance" — is broken exactly the way the FraxLend leftover-shares bug broke "shares written off from pool totals == shares removed from the borrower." In both cases, a shortfall/excess is silently absorbed by capping one side of the ledger (pool totals / builder balance) while the other side (borrower's shares / withdrawal amount promised to the EL) is left unadjusted.

### Impact Explanation
If a `BuilderPendingWithdrawal.amount` can ever exceed `state.builders[builder_index].balance` at the time `process_withdrawals` runs (e.g., because the builder's balance was reduced by an intervening builder-sweep withdrawal, another pending withdrawal processed earlier in the same block, or any other balance-reducing operation between the payment being queued and the withdrawal being applied), the committed `Withdrawal.amount` sent to the execution layer will exceed the amount actually deducted from CL state. Because withdrawals are validated by the EL as exact matches (`assert list(payload.withdrawals) == expected.withdrawals` pattern used across forks) and the EL mints the full `amount` to `address` regardless of CL-side capping, this creates Gwei out of thin air paid to the builder's `fee_recipient` — a non-owner gaining value not backed by any deducted balance. This satisfies the Critical impact bar ("Gwei created ... and paid to a non-owner").

### Likelihood Explanation
Likelihood depends on whether `BuilderPendingWithdrawal.amount` can exceed the builder's balance at processing time through any spec-following sequence of state transitions (no malicious peer/client needed — this is a question of whether the spec itself allows the queued amount and the balance to diverge before the queued item is drained). `process_builder_pending_payments` creates these withdrawal-queue entries from `builder_pending_payments`, and multiple such entries (or a builder-sweep) can interleave with a pending withdrawal for the same builder across epochs before it is dequeued, since `BUILDER_PENDING_WITHDRAWALS_LIMIT` allows up to `2^20` pending entries and processing is rate-limited to `MAX_WITHDRAWALS_PER_PAYLOAD - 1` per block. I was not able to fully trace every path that populates/consumes `builder_pending_payments` and confirm or rule out a balance-sufficiency check at payment-creation time within the scope of this review, so likelihood should be treated as **uncertain pending confirmation** that no invariant elsewhere in the epoch/block processing guarantees `sum(pending withdrawal amounts for a builder) <= builder.balance` at all times before dequeue.

### Recommendation
Either:
1. In `get_builder_withdrawals`, cap the `Withdrawal.amount` placed into `payload_expected_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` so the committed EL payout can never exceed the deducted amount, or
2. In `apply_withdrawals`, if `withdrawal.amount > builder_balance`, treat this as an invalid state transition, or
3. Add an explicit invariant/assertion (enforced at the point pending withdrawals are enqueued, and re-verified at dequeue time) that a builder's total queued pending-withdrawal amount never exceeds its current balance, closing the same class of gap that the FraxLend report identifies (leftover write-off decoupled from the amount actually charged/paid).

### Proof of Concept
Conceptual trace mirroring the FraxLend PoC structure (bad-debt write-off without corresponding deduction):
1. Builder B has `balance = 1 ETH`.
2. A `BuilderPendingWithdrawal` for B with `amount = 5 ETH` is queued (e.g., via `process_builder_pending_payments`, or because balance was reduced by an intervening operation after the payment was queued but before the withdrawal is processed).
3. In `process_withdrawals` → `get_builder_withdrawals`, a `Withdrawal(address=B.fee_recipient, amount=5 ETH)` is added to `payload_expected_withdrawals` — see `withdrawal.amount` used unmodified [1](#0-0) .
4. `apply_withdrawals` runs `state.builders[B].balance -= min(5 ETH, 1 ETH)` → builder balance becomes `0`, only `1 ETH` deducted [2](#0-1) .
5. The execution payload committed for this slot contains a withdrawal crediting `5 ETH` to `fee_recipient`, matching `payload_expected_withdrawals` per the honoring rule; the EL mints/credits the full `5 ETH`.
6. Net effect: `5 ETH` credited to `fee_recipient`, but only `1 ETH` removed from any tracked balance — `4 ETH` created from nothing, exactly analogous to FraxLend's repeated bad-debt write-off that reduces pool totals without taking anything from the liquidator/borrower.

The repo's own test `test_builder_withdrawal_insufficient_balance` [4](#0-3)  demonstrates steps 1–4 of this trace at the state level (`withdrawal.amount == 5 ETH` while `builders[0].balance` deduction is capped to `1 ETH`), confirming the root-cause mismatch exists in spec-conformant execution; what remains to verify (and is flagged above as uncertain) is whether any other part of the spec prevents step 2 (a pending withdrawal amount exceeding current balance) from ever arising through legitimate state transitions.

### Citations

**File:** specs/gloas/beacon-chain.md (L1821-1829)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1922-1931)
```markdown
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
