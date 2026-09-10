## Title
Builder withdrawal amount sent to the execution layer is not capped to the builder's actual balance, minting unbacked Gwei - (File: `specs/gloas/beacon-chain.md`)

## Summary
In `get_builder_withdrawals`, the `Withdrawal` object that is placed into `state.payload_expected_withdrawals` (the data structure that commits the beacon block to what the execution layer must credit) uses the **full requested** `withdrawal.amount` from `state.builder_pending_withdrawals`. The corresponding balance deduction, performed later in `apply_withdrawals`, instead uses `min(withdrawal.amount, builder_balance)`. When a builder's balance is smaller than the committed withdrawal amount, the execution layer mints the full committed amount to `fee_recipient` while the consensus layer only debits the builder's (smaller) actual balance — creating Gwei that was never backed by any account.

## Finding Description
`get_builder_withdrawals` builds the `Withdrawal` entries that become part of `state.payload_expected_withdrawals`, which is the CL's binding commitment to the EL for what balances must be credited: [1](#0-0) 

Note line 1827: `amount=withdrawal.amount` — the raw, requested amount stored in `BuilderPendingWithdrawal`, with **no cap** applied against `state.builders[builder_index].balance`.

Later, `apply_withdrawals` performs the actual CL-side balance mutation: [2](#0-1) 

Here the deduction is explicitly capped: `state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)`.

This asymmetry is confirmed directly by the project's own test suite, which documents the exact mismatch as expected/passing behavior: [3](#0-2) 

The test explicitly states: `withdrawal.amount: 5 ETH (requested amount)` is what ends up in `payload_expected_withdrawals`, while `builders[0].balance: 0 (deduction capped to available balance)` — i.e. only 1 ETH is actually debited from the builder even though 5 ETH is committed to the execution layer for `fee_recipient`.

This breaks the fundamental equality that every `Withdrawal.amount` sent to the EL (which mints/credits that exact amount to `fee_recipient` with no further checks — withdrawals are unconditional value transfers on the EL side) must be matched by an equal reduction somewhere in CL-tracked balances. The `min()` cap in `apply_withdrawals` guarantees this equality is violated whenever `builder.balance < withdrawal.amount`: `amount - builder.balance` Gwei is credited to `fee_recipient` with no account debited for it.

Contrast this with the (correctly handled) validator sweep path, `decrease_balance`, which also saturates at zero — but there, the amount placed into the `Withdrawal` object for full/partial validator withdrawals is always derived from `balance` itself (`amount=balance` or `amount=balance - max_effective_balance`), so the committed amount can never exceed what is subsequently deducted. The gloas builder-pending-withdrawal path is the only place where the *committed* (EL-facing) amount and the *deducted* (CL-facing) amount can diverge.

## Impact Explanation
This is a **Critical** severity issue under "Gwei created ... or paid to a non-owner" / "a payload or payment applied that the block did not commit to" (inverted: the block commits to paying more than is actually debited). Every time a `BuilderPendingWithdrawal.amount` exceeds the builder's balance at settlement time, the difference is minted on the execution layer to an arbitrary `fee_recipient` with no corresponding consensus-layer debit anywhere — a direct, protocol-level supply-inflation bug, not merely an accounting inconsistency. Because withdrawals are unconditionally credited on the EL with no possibility of reversion, this loss is immediate and permanent once the block is included.

## Likelihood Explanation
Reaching a state where a queued `BuilderPendingWithdrawal.amount` exceeds the builder's current balance does not require a malicious peer or client bug — it can arise from ordinary protocol dynamics: `builder_pending_withdrawals` entries are enqueued from `builder_pending_payments` after bid settlement (`process_builder_pending_payments`), and a builder's balance can decrease between the time a payment is queued and the time it is dequeued and applied (e.g., through other concurrent withdrawals, multiple queued payments for the same builder that jointly exceed balance, or builder-exit/consolidation flows). The balance-sufficiency checks in `process_execution_payload_bid` (`can_builder_cover_bid`) are evaluated only at bid time, not re-validated at settlement/withdrawal-processing time, so any gap between commitment and settlement across blocks/epochs is a viable path. The test suite even encodes this scenario as expected behavior, indicating it is a design gap rather than a hypothetical edge case.

## Recommendation
Cap the amount placed into the `Withdrawal` object in `get_builder_withdrawals` to the builder's current balance, mirroring what `apply_withdrawals` already does — i.e. compute `amount=min(withdrawal.amount, state.builders[builder_index].balance)` when constructing the `Withdrawal`, so the EL-committed amount and the CL-debited amount are always equal. Alternatively, ensure `apply_withdrawals` never diverges from what was computed in `get_builder_withdrawals` by removing the redundant `min()` there and instead enforcing the cap once, at withdrawal-construction time.

## Proof of Concept
1. Register a builder with balance `B` (e.g., 1 ETH).
2. Cause `state.builder_pending_withdrawals` to contain an entry for this builder with `amount = A > B` (e.g., 5 ETH) — reachable via normal `process_builder_pending_payments` settlement flow when the builder's balance has decreased since the payment was queued.
3. Call `process_withdrawals(state)` (as exercised by `test_builder_withdrawal_insufficient_balance` in [3](#0-2) ).
4. Observe: `state.payload_expected_withdrawals` contains a `Withdrawal` with `amount = A` (5 ETH) destined for `fee_recipient` — this is what the execution layer will credit — while `state.builders[builder_index].balance` is reduced only by `B` (1 ETH, capped via `min()` in `apply_withdrawals`). The difference `A - B` (4 ETH) is credited on the EL with no corresponding CL debit anywhere in the system.

### Citations

**File:** specs/gloas/beacon-chain.md (L1802-1834)
```markdown
##### New `get_builder_withdrawals`

```python
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
