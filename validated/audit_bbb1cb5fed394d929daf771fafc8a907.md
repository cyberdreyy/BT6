### Title
Uncapped builder-withdrawal amount in `get_builder_withdrawals` mints Gwei not backed by an equal balance decrease - (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_builder_withdrawals` places the fully requested `withdrawal.amount` (taken verbatim from `state.builder_pending_withdrawals`) into `state.payload_expected_withdrawals`, which the execution layer is contractually required to pay out in full. However, `apply_withdrawals` only debits the builder's internal balance by `min(withdrawal.amount, builder_balance)`. If the builder's balance drops below the committed amount between bid-time (when `can_builder_cover_bid` checked sufficiency) and settlement time, the EL mints the full committed amount while the CL only removes the smaller, capped amount from `state.builders[...].balance` — an asymmetry structurally identical to the reported Fluid bug, where the amount sent to the user was not capped even though the internal-accounting deduction was.

### Finding Description
The withdrawal creation path is: [1](#0-0) 

`Withdrawal.amount` is set to the uncapped `withdrawal.amount` from `state.builder_pending_withdrawals`, without checking `state.builders[builder_index].balance` at all.

The corresponding application path caps the *internal* deduction only: [2](#0-1) 

`state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)` — exactly the same shape as the vulnerable Fluid pattern where `withdrawAmountRaw_` was capped to `tokenRawSupply_` but the value transferred out (`supplyAmount_`) was left uncapped.

The committed `Withdrawal.amount` becomes part of `state.payload_expected_withdrawals`, which per the spec's own note the execution layer "is required to honor... in the execution layer": [3](#0-2) 

The spec authors are aware of exactly this asymmetry and explicitly document it as an accepted "net supply inflation" for the validator-sweep case: [4](#0-3) 

The builder-payment amount is fixed at bid-commit time via `settle_builder_payment` / `process_builder_pending_payments`, which append `payment.withdrawal` (a value frozen with the bid's requested `value`) to `state.builder_pending_withdrawals`: [5](#0-4) [6](#0-5) 

`can_builder_cover_bid` only checks sufficiency at bid-acceptance time: [7](#0-6) 

Between that check and the eventual settlement/withdrawal (which can span multiple slots/epochs, as demonstrated in `test_builder_payment_after_missed_epochs`), the builder's balance can be reduced by other builder-exit/withdrawal machinery (e.g. multiple concurrent pending payments/withdrawals accumulating against the same builder, since `get_pending_balance_to_withdraw_for_builder` is advisory and not strictly enforced at every mutation point). When settlement finally occurs with insufficient balance, `apply_withdrawals` silently caps the deduction while the committed `Withdrawal.amount` in the payload stays at the full, larger value.

### Impact Explanation
This breaks the "no Gwei created from nothing" invariant: the execution layer pays out the full `withdrawal.amount` to `fee_recipient` while `state.builders[...].balance` is decreased by less (potentially by zero, if the builder's balance is already exhausted). All spec-following nodes compute the same (under-collateralized) `state.payload_expected_withdrawals`, so this is not a fork/consensus-divergence bug — it is a protocol-level Gwei-creation bug: value paid out on L1 exceeds value actually removed from beacon-chain-tracked builder balance, i.e. an unbacked mint, which the report classifies as Critical ("Gwei created, destroyed or paid to a non-owner").

### Likelihood Explanation
Requires only that a builder's committed pending-withdrawal amount be locked in while the builder's tracked balance subsequently falls below that amount before the withdrawal is applied — a state reachable without any malicious coalition, purely through normal builder exit/payment sequencing (the repo's own test `test_builder_payment_after_missed_epochs` demonstrates multi-epoch delay between commit and settlement). No attacker collusion or majority stake is needed; it's a bookkeeping gap between commitment time and settlement time.

### Recommendation
Cap the `Withdrawal.amount` produced in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` at the time the withdrawal is created (mirroring what `get_builders_sweep_withdrawals` and `get_pending_partial_withdrawals` already do for their respective amounts), so the amount committed to the execution payload can never exceed what `apply_withdrawals` will actually deduct from the builder's balance.

### Proof of Concept
Conceptual state trace:
1. Builder `B` has `balance = 10 ETH`. Builder submits a bid with `value = 8 ETH`; `can_builder_cover_bid` passes (`10 - MIN_DEPOSIT_AMOUNT >= 8` assuming sufficient margin).
2. `settle_builder_payment` appends `BuilderPendingWithdrawal(builder_index=B, amount=8 ETH, fee_recipient=F)` to `state.builder_pending_withdrawals`.
3. Before this entry is processed by `get_builder_withdrawals`, further payments/logic reduce `state.builders[B].balance` to `2 ETH` (e.g., additional pending payments settle against the same builder across several slots before the first one's withdrawal is swept, since `builder_pending_withdrawals` can accumulate several entries for the same builder that jointly exceed current balance).
4. `get_builder_withdrawals` still emits `Withdrawal(validator_index=B, address=F, amount=8 ETH)` unconditionally — [8](#0-7) .
5. This entry goes into `state.payload_expected_withdrawals`; the execution layer mints `8 ETH` to `F`.
6. `apply_withdrawals` computes `state.builders[B].balance -= min(8 ETH, 2 ETH)` → balance becomes `0`, i.e., only `2 ETH` was actually removed from CL-tracked supply — [9](#0-8) .
7. Net effect: `6 ETH` was paid out on the execution layer with no corresponding decrease anywhere in beacon-state-tracked balances — Gwei created from nothing.

### Citations

**File:** specs/gloas/beacon-chain.md (L1167-1179)
```markdown
#### New `can_builder_cover_bid`

```python
def can_builder_cover_bid(
    state: BeaconState, builder_index: BuilderIndex, bid_amount: Gwei
) -> bool:
    builder_balance = state.builders[builder_index].balance
    pending_withdrawals_amount = get_pending_balance_to_withdraw_for_builder(state, builder_index)
    min_balance = MIN_DEPOSIT_AMOUNT + pending_withdrawals_amount
    if builder_balance < min_balance:
        return False
    return builder_balance - min_balance >= bid_amount
```
```

**File:** specs/gloas/beacon-chain.md (L1518-1527)
```markdown
#### New `settle_builder_payment`

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```
```

**File:** specs/gloas/beacon-chain.md (L1664-1677)
```markdown
def process_builder_pending_payments(state: BeaconState) -> None:
    """
    Processes the builder pending payments from the previous epoch.
    """
    quorum = get_builder_payment_quorum_threshold(state)
    for payment in state.builder_pending_payments[:SLOTS_PER_EPOCH]:
        if payment.weight >= quorum:
            state.builder_pending_withdrawals.append(payment.withdrawal)

    old_payments = state.builder_pending_payments[SLOTS_PER_EPOCH:]
    state.builder_pending_payments[:SLOTS_PER_EPOCH] = old_payments
    new_payments = [BuilderPendingPayment.empty() for _ in range(SLOTS_PER_EPOCH)]
    state.builder_pending_payments[SLOTS_PER_EPOCH:] = new_payments
```
```

**File:** specs/gloas/beacon-chain.md (L1805-1834)
```markdown
def get_builder_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
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

**File:** specs/gloas/beacon-chain.md (L1969-1972)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
```

**File:** specs/gloas/beacon-chain.md (L1977-1989)
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
