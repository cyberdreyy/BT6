### Title
Builder pending-withdrawal amount written to `payload_expected_withdrawals` is uncapped, while the balance deduction is capped — this mints Gwei to the builder's `fee_recipient` beyond the builder's actual balance ([File: specs/gloas/beacon-chain.md])

### Summary
In the Gloas fork, `get_builder_withdrawals` constructs each `Withdrawal` entry using the *raw requested* `withdrawal.amount` taken from `state.builder_pending_withdrawals`, without capping it to the builder's actual balance. That `Withdrawal` object (with the uncapped amount) is placed into `state.payload_expected_withdrawals`, which is the value committed to and paid out by the execution layer. Meanwhile, `apply_withdrawals` only decreases the builder's own beacon-state balance by `min(withdrawal.amount, builder_balance)`. This is structurally the same class of bug as the reported Lido issue: the code that mutates the accounting ("balance decreased") uses a capped/actual amount, while the code that determines the amount paid out to a third party uses the uncapped/requested amount — so the two diverge whenever the builder's balance is less than the requested withdrawal amount.

### Finding Description
`get_builder_withdrawals` builds withdrawal records straight from the queue entry’s `amount` field with no cap: [1](#0-0) 

`apply_withdrawals`, which actually mutates state, caps the deduction to the builder's live balance: [2](#0-1) 

The `Withdrawal` objects produced by `get_builder_withdrawals` (uncapped) flow directly into `state.payload_expected_withdrawals` via `get_expected_withdrawals`/`update_payload_expected_withdrawals`: [3](#0-2) [4](#0-3) 

`payload_expected_withdrawals` is the canonical withdrawals list that the execution layer is required to honor for the corresponding execution payload (per the block-processing note in the spec): [5](#0-4) 

On the execution layer, `Withdrawal.amount` is unconditionally credited to `Withdrawal.address` — there is no notion of "capping" on the EL side; the EL simply trusts the CL-provided withdrawals list. So if the CL emits a `Withdrawal(amount=X, address=fee_recipient)` while only deducting `min(X, balance) < X` from the builder's CL-tracked balance, then `X - min(X, balance)` Gwei is created out of nothing and paid to `fee_recipient`, with no corresponding decrease anywhere in the beacon state's balance ledger.

This exact discrepancy is explicitly exercised (and its resulting mismatch explicitly documented) by the repo's own spec tests, which construct a builder with insufficient balance and assert that the withdrawal amount recorded still equals the *full requested amount* while the balance deduction is capped to what's available: [6](#0-5) [7](#0-6) 

Contrast this with the sibling function for the validator sweep, which correctly recomputes `balance` via `get_balance_after_withdrawals` before deciding the withdrawal amount, so the emitted `Withdrawal.amount` can never exceed what is actually available: [8](#0-7) 

Builders queue up `BuilderPendingWithdrawal` entries via `process_execution_payload_bid` (bid accepted → `BuilderPendingPayment`) and `settle_builder_payment` (payment → withdrawal queue entry), based on a balance check performed only at bid-acceptance time: [9](#0-8) [10](#0-9) 

Because a builder can have several distinct pending payments/withdrawals queued concurrently (one per accepted bid, across multiple slots, each independently checked against balance at accept-time via `can_builder_cover_bid`), and because there can be a multi-epoch delay between bid acceptance and the eventual settlement/withdrawal-sweep, the aggregate of "already-promised but not-yet-swept" withdrawal amounts for one builder is not re-verified against the *current* balance at sweep time. I was unable to fully confirm within the available time whether `can_builder_cover_bid`'s check of outstanding pending payments/withdrawals is airtight against every path that could shrink a builder's `balance` between bid-acceptance and the corresponding withdrawal being processed (e.g., ordering of multiple pending withdrawals processed within the same block sweep, or a `builder_exit`/other builder-balance-affecting operation landing in between) — this would need to be verified with a concrete state-machine trace or additional code reading of `can_builder_cover_bid`/`get_pending_balance_to_withdraw_for_builder`, which I did not get to inspect their bodies before running out of iterations.

### Impact Explanation
If reachable, this breaks a Critical-tier equality explicitly listed as in-scope: "Gwei created ... paid to a non-owner." The execution layer would credit `fee_recipient` with more ETH than was ever debited from the builder's tracked balance in the beacon state — a direct, unbacked ETH mint with no path required through a malicious peer, partition, or coalition; a single builder submitting normal bids under legitimate protocol rules could trigger it if the balance-sufficiency invariant across the pending-payment lifecycle is not perfectly maintained.

### Likelihood Explanation
Moderate-to-uncertain. The bug's *mechanics* (uncapped emitted `Withdrawal.amount` vs. capped balance deduction) are proven directly from the spec text and are even codified as "expected" behavior in the repo's own test suite. What remains unconfirmed is whether the *protocol-level* invariant enforced at bid-acceptance (`can_builder_cover_bid`) is sufficient to prevent a builder's balance from ever legitimately dropping below the sum of its outstanding queued withdrawal amounts by the time they are swept — i.e., whether the "insufficient balance" scenario tested only via direct state manipulation in the unit tests is actually reachable through normal bid submission and multi-epoch settlement timing. Without confirming that, this should be treated as a suspected/candidate finding rather than a proven exploit path.

### Recommendation
Make `get_builder_withdrawals` (and any other builder-payout paths) cap the emitted `Withdrawal.amount` to the builder's live/remaining balance at construction time (analogous to how `get_pending_partial_withdrawals`/`get_validators_sweep_withdrawals` use `get_balance_after_withdrawals` to net out prior withdrawals within the same batch before computing the amount), so the value written into `payload_expected_withdrawals` can never exceed what `apply_withdrawals` actually deducts. Alternatively/additionally, formally verify (and add an invariant/assert) that `sum(outstanding builder_pending_payments + builder_pending_withdrawals amounts for a builder) <= builder.balance` is maintained at every state transition, closing off any path where the two diverge.

### Proof of Concept
Conceptual trace (mirrors the existing spec test, but framed as a protocol-level PoC):
1. Builder B submits and gets accepted bids across several slots such that the sum of accepted bid values is validated against balance at each acceptance time via `can_builder_cover_bid`.
2. Before all corresponding `BuilderPendingWithdrawal` entries are swept (`get_builder_withdrawals`/`apply_withdrawals`), B's balance is reduced by some other legitimate path not fully reflected in the "outstanding" accounting used by `can_builder_cover_bid` (candidate paths to confirm: overlapping in-flight pending payments not yet counted, builder exit processing, or ordering effects across multiple pending withdrawals processed in the same sweep without recomputing balance-after-prior-withdrawals the way validator sweeps do).
3. When the queued withdrawal is swept, `get_builder_withdrawals` emits `Withdrawal(amount=requested_amount, address=fee_recipient)` unchanged, while `apply_withdrawals` deducts only `min(requested_amount, current_balance)`.
4. The execution layer credits `fee_recipient` with the full `requested_amount`, while the beacon state only debited `current_balance` — the difference is Gwei created without a matching source, directly demonstrated by the repo's own test assertions: [11](#0-10) 

This PoC establishes the mechanical divergence conclusively; establishing that step 2 is reachable through unprivileged, protocol-legal bid submission alone (rather than direct state manipulation) requires further code review of `can_builder_cover_bid` and `get_pending_balance_to_withdraw_for_builder`, which could not be completed in this session.

### Citations

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

**File:** specs/gloas/beacon-chain.md (L1876-1918)
```markdown
##### Modified `get_expected_withdrawals`

```python
def get_expected_withdrawals(state: BeaconState) -> ExpectedWithdrawals:
    withdrawal_index = state.next_withdrawal_index
    withdrawals: list[Withdrawal] = []

    # [New in Gloas:EIP7732]
    # Get builder withdrawals
    builder_withdrawals, withdrawal_index, processed_builder_withdrawals_count = (
        get_builder_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(builder_withdrawals)

    # Get partial withdrawals
    partial_withdrawals, withdrawal_index, processed_partial_withdrawals_count = (
        get_pending_partial_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(partial_withdrawals)

    # [New in Gloas:EIP7732]
    # Get builders sweep withdrawals
    builders_sweep_withdrawals, withdrawal_index, processed_builders_sweep_count = (
        get_builders_sweep_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(builders_sweep_withdrawals)

    # Get validators sweep withdrawals
    validators_sweep_withdrawals, withdrawal_index, processed_validators_sweep_count = (
        get_validators_sweep_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(validators_sweep_withdrawals)

    return ExpectedWithdrawals(
        withdrawals,
        # [New in Gloas:EIP7732]
        processed_builder_withdrawals_count,
        processed_partial_withdrawals_count,
        # [New in Gloas:EIP7732]
        processed_builders_sweep_count,
        processed_validators_sweep_count,
    )
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

**File:** specs/gloas/beacon-chain.md (L1934-1941)
```markdown
##### New `update_payload_expected_withdrawals`

```python
def update_payload_expected_withdrawals(
    state: BeaconState, withdrawals: Sequence[Withdrawal]
) -> None:
    state.payload_expected_withdrawals = Withdrawals(data=withdrawals)
```
```

**File:** specs/gloas/beacon-chain.md (L1967-1980)
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
```

**File:** specs/gloas/beacon-chain.md (L2084-2141)
```markdown
##### New `process_execution_payload_bid`

```python
def process_execution_payload_bid(
    state: BeaconState, signed_bid: SignedExecutionPayloadBid
) -> None:
    bid = signed_bid.message
    builder_index = bid.builder_index
    amount = bid.value

    # For self-builds, amount must be zero regardless of withdrawal credential prefix
    if builder_index == BUILDER_INDEX_SELF_BUILD:
        assert amount == 0
        assert signed_bid.signature == bls.G2_POINT_AT_INFINITY
    else:
        # Verify that the builder is active
        assert is_active_builder(state, builder_index)
        # Verify that the builder is a payload builder
        assert state.builders[builder_index].version == PAYLOAD_BUILDER_VERSION
        # Verify that the builder has funds to cover the bid
        assert can_builder_cover_bid(state, builder_index, amount)
        # Verify that the bid signature is valid
        assert verify_execution_payload_bid_signature(state, signed_bid)

    # Verify commitments are under limit
    assert (
        len(bid.blob_kzg_commitments)
        <= get_blob_parameters(get_current_epoch(state)).max_blobs_per_block
    )

    # Verify that the bid is for the current slot
    assert bid.slot == state.slot
    assert state.slot > GENESIS_SLOT
    # Verify that the bid is for the right parent block
    assert bid.parent_block_hash == state.latest_block_hash
    # Verify that the bid's block hash differs from its parent block hash
    assert bid.block_hash != bid.parent_block_hash
    assert bid.parent_block_root == get_block_root_at_slot(state, state.slot - 1)
    assert bid.prev_randao == get_randao_mix(state, get_current_epoch(state))

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

    # Cache the signed execution payload bid
    state.latest_execution_payload_bid = bid
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

**File:** specs/electra/beacon-chain.md (L1427-1449)
```markdown
        validator = state.validators[validator_index]
        balance = get_balance_after_withdrawals(state, validator_index, all_withdrawals)
        if is_fully_withdrawable_validator(validator, balance, epoch):
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=validator_index,
                    address=ExecutionAddress(validator.withdrawal_credentials[12:]),
                    amount=balance,
                )
            )
            withdrawal_index += 1
        elif is_partially_withdrawable_validator(validator, balance):
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=validator_index,
                    address=ExecutionAddress(validator.withdrawal_credentials[12:]),
                    # [Modified in Electra:EIP7251]
                    amount=balance - get_max_effective_balance(validator),
                )
            )
            withdrawal_index += 1
```
