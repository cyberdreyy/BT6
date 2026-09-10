### Title
Builder withdrawal amount is not capped to available balance before being committed to the execution layer, allowing the execution layer to mint uncollateralized Gwei to the `fee_recipient` - (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_builder_withdrawals()` places the *full requested* `builder_pending_withdrawals[i].amount` into the committed `Withdrawal.amount` field, but `apply_withdrawals()` only deducts `min(withdrawal.amount, builder.balance)` from the builder's consensus-layer balance. Since the execution layer is required to honor `state.payload_expected_withdrawals` verbatim (crediting the committed `amount` to `address` unconditionally), any case where `builder.balance < withdrawal.amount` at settlement time causes the EL to mint Gwei that was never actually backed by a corresponding balance decrease on the CL side. This is the same equality violation as the reported `_payNative()` bug: the value **committed/paid out** diverges from the value **actually deducted from the payer**, with the surplus silently created and handed to a `fee_recipient`.

### Finding Description
In `specs/gloas/beacon-chain.md`, `get_builder_withdrawals` builds the `Withdrawal` object using the raw, uncapped pending amount: [1](#0-0) 

```python
for withdrawal in state.builder_pending_withdrawals:
    ...
    withdrawals.append(
        Withdrawal(
            index=withdrawal_index,
            validator_index=convert_builder_index_to_validator_index(builder_index),
            address=withdrawal.fee_recipient,
            amount=withdrawal.amount,
        )
    )
```

This `Withdrawal.amount` is not compared against `state.builders[builder_index].balance` here at all.

Then `apply_withdrawals` (modified in Gloas) deducts only the *capped* amount from the builder's balance: [2](#0-1) 

```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
```

The resulting `Withdrawal` (with the *uncapped* `amount`) is exactly the entry placed into `state.payload_expected_withdrawals`, which per the spec's own note is binding on the execution layer: [3](#0-2) 

"any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer ... Since the execution layer mints the full committed amount regardless, any CL-side saturation creates a net supply inflation."

Note that this paragraph is written for the *validator* full/partial-withdrawal saturation case (`decrease_balance` clamps to zero), and the spec authors explicitly acknowledge that scenario causes "net supply inflation" as an accepted, documented tradeoff. However, the **builder pending-withdrawal path has the identical structural flaw** but is not called out in that note, and — unlike the validator sweep path, whose amounts are derived from live balance at commit-time — the builder-withdrawal amount is a *requested* value that can legitimately diverge from balance for reasons unrelated to `decrease_balance`'s epoch-boundary saturation:

- `get_builder_withdrawals` is called at withdrawal-commitment time using `withdrawal.amount` stored earlier in `state.builder_pending_withdrawals` (queued potentially several slots/epochs earlier via `settle_builder_payment`), while the actual `builder.balance` used at `apply_withdrawals` time is read from the *current* state.
- Between queuing (via `process_execution_payload_bid` → `BuilderPendingPayment` → `settle_builder_payment` → `builder_pending_withdrawals`) and settlement, the builder can incur *further* bids/payments. `can_builder_cover_bid` does attempt to net out `get_pending_balance_to_withdraw_for_builder`, but this accounting is spread across `builder_pending_payments` (a fixed-size, per-slot indexed rolling array) and `builder_pending_withdrawals` (a FIFO list), several epochs apart, with eviction logic (`process_builder_pending_payments`) that can drop stale entries — the exact scenario tested by `test_builder_payment_after_missed_epochs`, where a payment is force-appended directly to `builder_pending_withdrawals` after being evicted from `builder_pending_payments`. Any accounting gap between these structures and the true balance at withdrawal-processing time reproduces the insufficient-balance state.

The behavior is exercised directly by the existing spec test suite, which documents it as expected/by-design rather than flagging it as a bug: [4](#0-3) 

```python
def test_builder_withdrawal_insufficient_balance(spec, state):
    ...
    Output State Verified:
        - withdrawal.amount: 5 ETH (requested amount)
        - builders[0].balance: 0 (deduction capped to available balance)
    ...
    assert_process_withdrawals(
        ...
        builder_balances={builder_index: 0},
        withdrawal_amounts_builders={builder_index: withdrawal_amount},  # 5 ETH committed, 1 ETH deducted
    )
```

This proves the equality break directly: the committed `Withdrawal.amount` (5 ETH, which the EL will mint to `fee_recipient`) is 5x the amount actually removed from the builder (1 ETH → 0). 4 ETH of Gwei is created without any corresponding decrease anywhere in `state`.

### Impact Explanation
This breaks the "Gwei created ... paid to a non-owner" equality: the total ETH minted by the execution layer for a block's withdrawals must equal the total decrease in consensus-layer accounted balances (builder + validator), and here it does not. The excess is paid to `withdrawal.fee_recipient`, an address wholly controlled by whoever the builder designated it to (via `BuilderPendingWithdrawal.fee_recipient`, independent of the builder's own `execution_address`). This is a Critical-class impact per the given rubric: unbacked ETH creation and payment to an arbitrary address, applied by a spec-following block that every honest node/EL must accept as valid (since `process_withdrawals`/`apply_withdrawals`/`get_builder_withdrawals` are deterministic pure-spec functions — no malicious client or coalition is required, only a builder ending up momentarily under-collateralized relative to its queued pending-withdrawal amount).

### Likelihood Explanation
Likelihood depends on whether `can_builder_cover_bid`'s netting of `builder_pending_payments` + `builder_pending_withdrawals` is airtight across all block-processing/epoch-transition paths (payment eviction, missed-slot handling, multiple concurrent bids). The repository's own test (`test_builder_payment_after_missed_epochs`) shows the payment/withdrawal bookkeeping already requires special-cased handling for evicted payments across missed epochs, which is exactly the kind of edge case that can desynchronize the "conservatively required balance" check from the eventual settlement amount. I could not, within the available context, fully trace every call path that mutates `state.builders[*].balance` (e.g. builder deposit/exit requests) to conclusively prove a normal (non-adversarial) sequence that pushes `builder.balance` below a previously-committed `builder_pending_withdrawals[i].amount`; this would need to be confirmed with a background agent that can execute the full spec test suite and enumerate all mutators of `Builder.balance`.

### Recommendation
Cap the `Withdrawal.amount` produced by `get_builder_withdrawals` (and, for consistency, all builder amounts) to the builder's *current* balance at commitment time, i.e. mirror what `get_validators_sweep_withdrawals`/`is_partially_withdrawable_validator` do by deriving the committed amount from live balance rather than a stale requested amount:
```python
amount=min(withdrawal.amount, state.builders[builder_index].balance)
```
so that the value placed into `state.payload_expected_withdrawals` (which the EL must mint) is always identical to the value that `apply_withdrawals` actually deducts, eliminating the possibility of net supply inflation for the builder-withdrawal path.

### Proof of Concept
Using the existing spec test harness pattern (`tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py::test_builder_withdrawal_insufficient_balance`):
1. Builder 0 has `balance = 1 ETH`.
2. `state.builder_pending_withdrawals` contains one entry for builder 0 with `amount = 5 ETH` and `fee_recipient = attacker_address`.
3. Call `spec.process_withdrawals(state)`.
4. Resulting `state.payload_expected_withdrawals[0]` has `amount = 5 ETH`, `address = attacker_address` — this is what the execution layer is bound to credit.
5. `state.builders[0].balance` becomes `0` — only `1 ETH` was actually removed.
6. Net effect: `4 ETH` of Gwei is created and paid to `attacker_address` with no corresponding decrease anywhere in `state`, breaking the CL/EL supply equality. [5](#0-4)

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1834)
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

**File:** specs/gloas/beacon-chain.md (L1967-1990)
```markdown
##### Modified `process_withdrawals`

*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

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
