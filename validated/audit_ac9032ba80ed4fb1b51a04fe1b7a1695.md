### Title
`get_builder_withdrawals()` commits the full requested amount to the execution layer while `apply_withdrawals()` only debits the capped/available balance — (File: specs/gloas/beacon-chain.md)

### Summary
In `specs/gloas/beacon-chain.md`, the withdrawal-construction path (`get_builder_withdrawals`) writes the **uncapped, requested** `BuilderPendingWithdrawal.amount` into the `Withdrawal.amount` field that is committed on-chain (`state.payload_expected_withdrawals`, and ultimately the execution payload's withdrawals list). Separately, `apply_withdrawals` only decreases `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`. When a builder's balance is smaller than the pending withdrawal's requested amount, the committed `Withdrawal.amount` (what the execution layer will actually credit to `fee_recipient`) is larger than what is ever debited from the builder's consensus-layer balance. This is structurally the same class of bug as the PoolTogether report: a value/record is "emitted"/committed for the full amount even though the actual backing effect only partially succeeded.

### Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md, lines ~1802–1834) builds the `Withdrawal` object using the raw, uncapped amount: [1](#0-0) 

That `Withdrawal` list becomes `state.payload_expected_withdrawals`, which the execution payload is required to match exactly (this is what the execution layer treats as authoritative and credits to the destination address): [2](#0-1) 

However, `apply_withdrawals` (the function that actually mutates consensus-layer state to "back" that withdrawal) caps the deduction to the builder's actual balance: [3](#0-2) 

The test suite explicitly documents and asserts this exact discrepancy as intended behavior: the committed `Withdrawal.amount` equals the full requested amount, while the balance deduction is capped ("processed even if capped"): [4](#0-3) 

The queue population path shows how `BuilderPendingWithdrawal.amount` can legitimately be less than or greater than the builder's `balance` at the time the withdrawal is settled — a pending payment is queued at bid time via `settle_builder_payment`, but is only *applied* (and its committed `Withdrawal` constructed) potentially epochs later in `process_withdrawals`, after the builder's balance could have changed (e.g., through other pending withdrawals consuming balance first, since `builder_pending_withdrawals` is a FIFO queue and multiple entries for the same builder can stack up before any of them are processed): [5](#0-4) [6](#0-5) 

This breaks the equality that must hold between what the CL commits/removes and what the EL mints/credits: the amount the execution layer pays out to `fee_recipient` must equal the amount actually removed from the payer's balance on the consensus layer. Here, `Withdrawal.amount` (paid by EL) can exceed `min(requested, balance)` (the only amount ever debited on the CL side).

### Impact Explanation
This is Gwei creation: the execution layer will mint/credit the full `Withdrawal.amount` to the `fee_recipient` address, while the consensus layer never removes that full amount from any account (`builders[builder_index].balance` floors at 0 and the excess is simply discarded, not tracked or re-queued). This is a direct violation of the "Gwei created" invariant, meeting the Critical bar in the rules (Gwei created, destroyed, or paid to a non-owner). Because every honest, spec-following node computes `get_expected_withdrawals`/`apply_withdrawals` identically, this is not a client bug or an off-spec issue — it is baked into the specified state-transition function itself and would be replicated identically by every conformant node, so it does not cause a fork; it is a genuine supply-invariant break agreed upon by all spec-following nodes.

### Likelihood Explanation
This requires no malicious coalition, adversarial validator behavior, or economic stake attack — it is purely a function of ordinary state evolution: a builder's balance decreasing (e.g., via other queued withdrawals draining it, or exits) between when a `BuilderPendingWithdrawal` is queued (at bid-settlement time via `settle_builder_payment` or `process_builder_pending_payments`) and when it is actually processed in a later block's `process_withdrawals`. The `can_builder_cover_bid` check (used at bid acceptance time) accounts for currently pending withdrawal amounts, but by the time multiple pending withdrawals are queued and processed sequentially in FIFO order, an already-committed `amount` in the queue can exceed what remains once earlier queue entries have been debited, since all commitments assume the pre-drain balance. This makes the condition realistically reachable in normal operation, not merely a theoretical edge case.

### Recommendation
Cap the committed `Withdrawal.amount` in `get_builder_withdrawals` to `min(withdrawal.amount, current available balance accounting for prior withdrawals in this same set)`, mirroring what `get_validators_sweep_withdrawals`/`get_pending_partial_withdrawals` do for validators (which compute `balance` via `get_balance_after_withdrawals` before deciding the withdrawal amount). This ensures the amount committed to and paid by the execution layer always matches the amount actually deducted from the builder's consensus-layer balance, preserving the Gwei invariant.

### Proof of Concept
1. A builder queues a `BuilderPendingWithdrawal` for amount `A` (e.g. via `settle_builder_payment` after a bid is honored).
2. Before this withdrawal is processed in `process_withdrawals`, the builder's `balance` is reduced below `A` by other means (e.g. earlier-queued withdrawals for the same builder being processed first, or across epochs where multiple payments accumulate faster than balance is replenished) — the test `test_builder_withdrawal_insufficient_balance` directly instantiates a state where a builder has only 1 ETH but a queued withdrawal amount of 5 ETH: [7](#0-6) 
3. `process_withdrawals` → `get_builder_withdrawals` constructs `Withdrawal(amount=5 ETH, address=fee_recipient, ...)` and places it into `state.payload_expected_withdrawals`, which the execution payload must match exactly.
4. `apply_withdrawals` deducts only `min(5 ETH, 1 ETH) = 1 ETH` from `builders[0].balance`, leaving it at 0.
5. The execution layer, per the committed withdrawal, credits `fee_recipient` the full 5 ETH — 4 ETH more than was ever removed from any consensus-layer account. This 4 ETH is created out of thin air relative to the total supply invariant that CL withdrawal amounts must equal CL balance decreases.

### Citations

**File:** specs/gloas/beacon-chain.md (L1151-1165)
```markdown
#### New `get_pending_balance_to_withdraw_for_builder`

```python
def get_pending_balance_to_withdraw_for_builder(
    state: BeaconState, builder_index: BuilderIndex
) -> Gwei:
    balance = Gwei(0)
    for withdrawal in state.builder_pending_withdrawals:
        if withdrawal.builder_index == builder_index:
            balance += withdrawal.amount
    for payment in state.builder_pending_payments:
        if payment.withdrawal.builder_index == builder_index:
            balance += payment.withdrawal.amount
    return balance
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

**File:** specs/gloas/beacon-chain.md (L1922-1932)
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
```

**File:** specs/gloas/beacon-chain.md (L1937-1941)
```markdown
def update_payload_expected_withdrawals(
    state: BeaconState, withdrawals: Sequence[Withdrawal]
) -> None:
    state.payload_expected_withdrawals = Withdrawals(data=withdrawals)
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
