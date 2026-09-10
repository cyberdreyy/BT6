Confirmed root cause: `get_builder_withdrawals` in `specs/gloas/beacon-chain.md` places the *uncapped, requested* `withdrawal.amount` into the withdrawal object that gets committed to the execution layer via `state.payload_expected_withdrawals`, while `apply_withdrawals` only deducts `min(withdrawal.amount, builder_balance)` from the builder's beacon-chain balance. This produces exactly the "amount computed/committed ≠ amount actually backed" pattern from the external report, but in the opposite (inflationary) direction: it can mint EL-side Gwei that is never actually debited on the CL side.

### Title
Builder withdrawal commits full requested amount to the execution layer while only debiting the capped available balance, minting unbacked Gwei - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies `withdrawal.amount` from `state.builder_pending_withdrawals` verbatim into the `Withdrawal` object that becomes part of `state.payload_expected_withdrawals` (the list the execution layer is required to honor). `apply_withdrawals`, however, deducts only `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`. If a builder's balance is ever smaller than its queued pending-withdrawal amount at the moment `process_withdrawals` runs, the committed `Withdrawal.amount` (fully minted/credited by the EL to `fee_recipient`) exceeds the Gwei actually removed from the builder, creating supply out of nothing.

### Finding Description
- `get_builder_withdrawals` (`specs/gloas/beacon-chain.md:1802-1834`) builds `Withdrawal(..., amount=withdrawal.amount)` directly from the queued `BuilderPendingWithdrawal.amount`, with no capping to `state.builders[builder_index].balance`. [1](#0-0) 
- `apply_withdrawals` (`specs/gloas/beacon-chain.md:1920-1932`) deducts `min(withdrawal.amount, builder_balance)` from the builder's balance — i.e., it silently caps the *debit* but not the *committed amount*. [2](#0-1) 
- The very test suite documents this exact behavior as intended: `test_builder_withdrawal_insufficient_balance` sets a builder balance of 1 ETH with a queued pending withdrawal of 5 ETH, and asserts the **withdrawal amount stays at 5 ETH** (the full requested amount, which becomes part of `payload_expected_withdrawals` and is what the EL will pay out) while the builder's on-chain balance is only reduced to 0 (a 1 ETH debit). [3](#0-2) 
- The spec authors explicitly acknowledge, for the *validator* sweep case, that "the execution layer mints the full committed amount regardless, any CL-side saturation creates a net supply inflation," and justify applying withdrawal debits *synchronously* within `process_withdrawals` to avoid the gap. That reasoning is not applied consistently to builder pending withdrawals: `get_builder_withdrawals` reads `builder.balance` nowhere, so there's no synchronization between the debit and the committed amount — the cap is applied only in `apply_withdrawals`, and the *committed* `Withdrawal.amount` field is never adjusted to match. [4](#0-3) 
- `can_builder_cover_bid`, used only at bid-acceptance time in `process_execution_payload_bid`, checks `builder_balance - (MIN_DEPOSIT_AMOUNT + pending_withdrawals_amount) >= bid_amount` before *queuing* a new pending payment. [5](#0-4) 
  This only guards the moment a bid is accepted; it provides no re-verification at the moment `apply_withdrawals`/`get_builder_withdrawals` actually executes the queued withdrawal several slots/epochs later. Builders cannot be slashed, but the spec never proves builder balance is monotonically sufficient for all outstanding queued pending withdrawals between bid-acceptance time and settlement time (e.g., across multiple concurrently-queued payments/pending withdrawals whose sum can exceed balance if any single check used a stale or race-prone snapshot, or if future builder-balance-reducing operations are introduced). The equality that should hold — "Gwei minted at the EL for a withdrawal == Gwei debited from the committer's beacon-chain balance" — is broken by construction in `get_builder_withdrawals`/`apply_withdrawals` whenever `withdrawal.amount > builder.balance` at settlement time.

### Impact Explanation
This breaks the "Gwei created ... paid to a non-owner" equality named in scope: the execution layer will credit `fee_recipient` (or `execution_address`, for the sweep case, which is capped correctly via `builder.balance` — only the pending-withdrawal path is uncapped in the committed amount) the full `withdrawal.amount`, while the beacon chain state removes strictly less than that from the builder. The shortfall is Gwei minted without a corresponding beacon-chain debit — inflating total supply and effectively paying the fee recipient with value that was never actually backed by the builder's stake. This matches "Critical - Gwei created ... or a payload executed or paid that the block did not commit to" since the actual debit committed on-chain (the balance decrease) diverges from the amount the block's withdrawals list (and hence the EL) actually pays out.

### Likelihood Explanation
Whether this is reachable in practice hinges entirely on whether `can_builder_cover_bid`'s single-point-in-time check at bid-acceptance is a sufficient invariant to guarantee `builder.balance >= sum(all pending amounts)` at every future settlement point, given the multi-slot delay between bid acceptance (`process_execution_payload_bid`), payment settlement (`settle_builder_payment`/`process_builder_pending_payments`), and final withdrawal execution (`process_withdrawals`). I was not able to fully trace every code path that mutates `state.builders[*].balance` (e.g. `process_builder_deposit_request`, `process_builder_exit_request`, quorum-based payment settlement across epoch boundaries) within the available index to prove or disprove that the invariant is always maintained. Given the index size limits, some file contents involved in that chain (e.g. the full `process_builder_pending_payments` epoch-processing logic and all builder balance mutation sites) may not be fully available to me.

### Recommendation
Cap the committed `Withdrawal.amount` in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` (mirroring what `get_pending_partial_withdrawals` already does for validators via `min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)`), so the amount the block commits to the execution layer always matches the amount actually debited from the builder in `apply_withdrawals`.

### Proof of Concept
1. A builder is active and, via `process_execution_payload_bid`, has a `BuilderPendingWithdrawal` queued in `state.builder_pending_withdrawals` for amount `A` (verified sufficient by `can_builder_cover_bid` at that time). [6](#0-5) 
2. Before the corresponding `process_withdrawals` call executes this queued withdrawal, the builder's `state.builders[builder_index].balance` is reduced below `A` (e.g., through other queued/settled payments/exits reducing balance in the interim).
3. `get_builder_withdrawals` still emits `Withdrawal(amount=A, address=fee_recipient, ...)` unmodified into `state.payload_expected_withdrawals`. [7](#0-6) 
4. `apply_withdrawals` only deducts `min(A, builder.balance)` — strictly less than `A` — from the builder. [8](#0-7) 
5. The execution layer, honoring `payload_expected_withdrawals`, credits the full `A` Gwei to `fee_recipient`, while only `min(A, balance) < A` Gwei was removed from the CL-tracked total supply — net Gwei creation. The unit test at `tests/.../test_process_withdrawals.py:116-156` demonstrates the exact numeric scenario (5 ETH committed, only 1 ETH actually debited).

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
