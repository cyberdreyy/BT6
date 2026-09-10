### Title
Uncapped Builder Withdrawal Amount Committed to Payload While CL Deduction Saturates at Balance — Gwei Minted Without Backing - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies the requested `amount` from `state.builder_pending_withdrawals` verbatim into the committed `Withdrawal` object that becomes part of `state.payload_expected_withdrawals`, without capping it to the builder's current balance. The actual balance deduction performed later by `apply_withdrawals` is capped via `min(withdrawal.amount, builder_balance)`. If a builder's balance has been reduced to less than the pending withdrawal amount by the time the withdrawal is processed (e.g. a builder sweep withdrawal zeroing the balance in between bid acceptance and payment settlement), the block commits an uncapped withdrawal amount that the execution layer is required to honor/mint in full, while the consensus layer only debits the smaller, saturated amount from the builder.

### Finding Description
`get_builder_withdrawals` builds each committed `Withdrawal` directly from `state.builder_pending_withdrawals[i].amount`: [1](#0-0) 

That withdrawal is included unmodified in `state.payload_expected_withdrawals` via `update_payload_expected_withdrawals`, which the spec states the execution layer "is required to honor" — i.e. it is a binding commitment that the EL must mint/pay out in full: [2](#0-1) 

However, the corresponding balance debit on the consensus layer side is explicitly saturated to the builder's *current* balance at apply time: [3](#0-2) 

The spec's own docstring for `process_withdrawals` acknowledges this exact class of bug for validators/consolidations ("any CL-side saturation creates a net supply inflation"), but the same saturation path exists independently for builders via `get_builders_sweep_withdrawals`, which can zero out `builder.balance` (withdrawing the builder's *full* balance) once `builder.withdrawable_epoch <= epoch`: [4](#0-3) 

Because `get_builder_withdrawals` (pending, priority 1) is processed *before* `get_builders_sweep_withdrawals` (priority 3) within the same call to `get_expected_withdrawals`, if a builder that becomes sweep-eligible had its balance already reduced by an earlier sweep at an earlier slot (balance already 0) while a not-yet-settled `builder_pending_withdrawals` entry for it still exists (e.g. queued from a bid accepted while the builder still had funds, then the builder's balance is drained by an earlier full sweep before this pending withdrawal is processed), the pending withdrawal is still emitted with its full original `amount` into the committed payload, while `apply_withdrawals` deducts `min(amount, 0) = 0` from the (already-zero) builder balance: [5](#0-4) 

The test suite explicitly documents and accepts this saturation-without-capping-the-committed-amount behavior as intended: [6](#0-5) [7](#0-6) 
(`test_all_builder_withdrawals_zero_balance` shows a builder with `balance = 0` still produces a withdrawal committed for the full `MIN_ACTIVATION_BALANCE` requested amount.)

This is structurally identical to the PoolTogether `claimRewards` bug: a payout function computes and transfers/commits an amount based on a stored "claim record" without re-validating it against the payer's actual remaining balance at redemption time, so more value can be paid out (here, minted by the EL to `fee_recipient`) than the payer (the builder) actually has or than the CL deducts.

### Impact Explanation
This breaks the Gwei-conservation equality between the consensus layer's accounting (`builders[i].balance` decreased by only the saturated amount) and the execution layer's minting obligation (paying the full committed `withdrawal.amount` from `payload_expected_withdrawals`, which the spec states must be honored). The result is Gwei created that is not backed by any corresponding CL-side balance decrease — net protocol-wide supply inflation, credited to the builder's arbitrary `fee_recipient`. This matches the Critical-tier impact criterion ("Gwei created, destroyed, or paid to a non-owner").

### Likelihood Explanation
Reaching this requires only ordinary, spec-following state transitions: a builder receives a bid payment queued as a `BuilderPendingWithdrawal`, and before that withdrawal is drained via `get_builder_withdrawals`, the same builder becomes sweep-eligible (`withdrawable_epoch <= epoch`, e.g. through `process_builder_exit_request`) and is swept to zero balance by an earlier block's `get_builders_sweep_withdrawals`. No malicious peer, coalition, or client bug is needed — a single builder or proposer sequencing blocks/exits in this order can trigger it, and the test suite treats the saturation as expected behavior rather than flagging the inconsistency between the committed and applied amounts. I was not able to fully verify all guard conditions in `can_builder_cover_bid` (function body not retrieved) that determine whether bid acceptance re-checks outstanding pending withdrawals against current balance at accept time versus at settlement/withdrawal time — this leaves some uncertainty about how tightly the window can be exploited, but the saturation logic in `apply_withdrawals` and the uncapped commitment in `get_builder_withdrawals` are independently confirmed in the spec text.

### Recommendation
Cap the committed `Withdrawal.amount` in `get_builder_withdrawals` (and any other builder-balance-derived withdrawal, including the direct-append path in `apply_parent_execution_payload`/EIP8205 `settle_builder_payment` fallback) to the builder's *current* balance at commitment time, mirroring the `min(withdrawal.amount, builder_balance)` logic already used in `apply_withdrawals`, so the amount placed into `payload_expected_withdrawals` (which the EL mints) can never exceed what the CL actually deducts.

### Proof of Concept
1. Builder `B` submits a winning bid; `process_execution_payload_bid` records a `BuilderPendingPayment` with `withdrawal.amount = V` (`specs/gloas/beacon-chain.md:2124-2137`).
2. The payment settles above quorum; `process_builder_pending_payments` appends `BuilderPendingWithdrawal(amount=V, builder_index=B, ...)` to `state.builder_pending_withdrawals` (`specs/gloas/beacon-chain.md:1664-1677`).
3. Before this entry is drained, `B.withdrawable_epoch <= current_epoch` (e.g., builder exited) and, in an earlier block, `get_builders_sweep_withdrawals` withdraws `B`'s *full* balance, setting `builders[B].balance = 0` (`specs/gloas/beacon-chain.md:1839-1874`, `1923-1932`).
4. In a subsequent block, `get_builder_withdrawals` still emits `Withdrawal(amount=V, ...)` for `B`'s pending entry, unconditionally, into `payload_expected_withdrawals` (`specs/gloas/beacon-chain.md:1801-1834`).
5. `apply_withdrawals` deducts `min(V, 0) = 0` from `builders[B].balance` (`specs/gloas/beacon-chain.md:1923-1932`), while the payload/EL commitment still shows `amount = V` owed to `fee_recipient` — `V` Gwei minted with no CL-side backing.

### Citations

**File:** specs/gloas/beacon-chain.md (L1801-1834)
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

**File:** specs/gloas/beacon-chain.md (L1839-1874)
```markdown
def get_builders_sweep_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    epoch = get_current_epoch(state)
    builders_limit = min(len(state.builders), MAX_BUILDERS_PER_WITHDRAWALS_SWEEP)
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
    builder_index = state.next_withdrawal_builder_index
    for _ in range(builders_limit):
        all_withdrawals = list(prior_withdrawals) + withdrawals
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if has_reached_limit:
            break

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
            withdrawal_index += 1

        builder_index = (builder_index + 1) % len(state.builders)
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```
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

**File:** specs/gloas/beacon-chain.md (L1969-1975)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L850-905)
```python
@with_gloas_and_later
@spec_state_test
def test_all_builder_withdrawals_zero_balance(spec, state):
    """
    Builders with zero balance - withdrawals still processed but with zero deduction.

    Input State Configured:
        - state.builders[0,1]: Builders exist with zero balance
        - builder_pending_withdrawals: 2 entries requesting MIN_ACTIVATION_BALANCE each
        - validators[0].withdrawable_epoch: <= current_epoch (sweep eligible)

    Note: regular_index=0 intentionally overlaps with builder_index=0 to demonstrate
    that builder indices and validator indices are separate namespaces.

    Output State Verified:
        - payload_expected_withdrawals: Contains 3 withdrawals (2 builder + 1 sweep)
        - Builder withdrawals processed (deduction capped to 0)
        - builders[0,1].balance: 0 (unchanged)
        - balances[0]: 0 (full withdrawal processed)
        - Note: Builder withdrawals always processed, amount capped to available balance
    """

    builder_indices = [0, 1]
    withdrawal_amount = spec.MIN_ACTIVATION_BALANCE
    regular_index = (
        0  # Same numeric index as builder 0, but different entity (validator vs builder)
    )

    prepare_process_withdrawals(
        spec,
        state,
        builder_indices=builder_indices,
        builder_withdrawal_amounts=dict.fromkeys(builder_indices, withdrawal_amount),
        builder_balances=dict.fromkeys(builder_indices, 0),
        full_withdrawal_indices=[regular_index],
    )

    pre_state = state.copy()
    yield from run_gloas_withdrawals_processing(spec, state)

    builder_validator_indices = [
        spec.convert_builder_index_to_validator_index(i) for i in builder_indices
    ]

    assert_process_withdrawals(
        spec,
        state,
        pre_state,
        withdrawal_count=3,
        withdrawal_order=builder_validator_indices + [regular_index],
        balances={regular_index: 0},
        builder_balances={0: 0, 1: 0},
        builder_pending_delta=-2,
        withdrawal_index_delta=3,
    )

```
