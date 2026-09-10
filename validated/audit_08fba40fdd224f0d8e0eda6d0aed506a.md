### Title
Builder withdrawal payload commits the uncapped requested amount while `apply_withdrawals` only debits the capped/available balance, allowing Gwei to be created for the builder's `fee_recipient` — (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` builds the `Withdrawal` object that becomes part of the block's binding `payload_expected_withdrawals` using the *raw requested* amount stored in `state.builder_pending_withdrawals[i].amount`, without capping it to the builder's current balance. `apply_withdrawals`, however, only decreases `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`. When a pending withdrawal's committed amount exceeds the builder's actual balance at processing time, the execution layer is instructed to credit the full (uncapped) amount to `fee_recipient`, while the consensus-layer stake is only reduced by the capped amount — creating Gwei with no corresponding balance decrease anywhere in the state. This is the exact analog of the Derby Vault bug: a payout/removal amount is derived from the *requested* value instead of the *actually available/realized* value, causing the two sides of the ledger to diverge.

### Finding Description
`get_builder_withdrawals` copies `withdrawal.amount` verbatim from `state.builder_pending_withdrawals` into the `Withdrawal` it emits: [1](#0-0) 

This `Withdrawal` (with the uncapped amount) is placed into `expected.withdrawals`, which is committed to `state.payload_expected_withdrawals` and is the set the execution layer is required to honor (paying `withdrawal.amount` to `withdrawal.address`), analogous to how Capella withdrawals bind the EL: [2](#0-1) 

But `apply_withdrawals` only decreases the builder's tracked stake by the capped, actually-available amount: [3](#0-2) 

The mismatch is explicitly exercised (and asserted as expected behavior) by the existing test suite: [4](#0-3) 

The test confirms: `payload_expected_withdrawals[0].amount == 5 ETH` (the requested amount), while `builders[0].balance == 0` (capped deduction from only 1 ETH available). The 4 ETH difference is paid out to `fee_recipient` on the execution layer with no offsetting decrease anywhere in `state.builders[*].balance` or `state.balances[*]` — this breaks the Gwei conservation invariant (`created == 0` for any state transition that isn't a deposit).

By contrast, for ordinary validators the spec is careful to always compute `Withdrawal.amount` bounded by the *current* balance at construction time (`amount=balance`, `amount=balance - MAX_EFFECTIVE_BALANCE`, or `min(balance - MIN_ACTIVATION_BALANCE, amount)`), so `decrease_balance` never needs to silently truncate a smaller amount than what was committed to the EL: [5](#0-4) [6](#0-5) 

The builder path is the outlier: it uses the *stale, requested* amount from `builder_pending_withdrawals` rather than re-deriving it from `min(requested, builder.balance)` at withdrawal-construction time.

### Impact Explanation
This directly matches the "Gwei created ... paid to a non-owner" Critical-impact category. If a builder's `builder_pending_withdrawals` amount ever exceeds their live balance when `process_withdrawals` runs, the committed payload instructs the execution layer to mint/credit the full requested amount to `fee_recipient`, while the consensus layer only removes the (smaller) available balance from the builder. The result is unbacked ETH credited on the execution layer — a protocol-wide supply-conservation violation, not merely an accounting-display bug (unlike the caveat already documented for validator withdrawals at `specs/gloas/beacon-chain.md:1976-1990`, which is bounded because `Withdrawal.amount` for validators is always pre-capped to at most `balance`).

### Likelihood Explanation
The likelihood hinges on how `builder_pending_withdrawals[i].amount` can come to exceed the builder's `balance` by the time it is processed. Candidates in-scope of the spec text include: multiple execution-payload bids from the same builder being individually validated against the builder's balance at bid-acceptance time (via a bid-coverage check) without reserving/locking funds for previously accepted-but-not-yet-settled bids, so several bids can each pass the coverage check against the same unspent balance before any of them are debited; once several of these later settle into `builder_pending_withdrawals` and are processed by `get_builder_withdrawals`, earlier withdrawals will have already drained the balance, leaving later pending withdrawals' committed amounts larger than what remains. I was not able to fully trace the exact bid-acceptance/reservation logic (`can_builder_cover_bid` and settlement queue capacity) within the available iterations, so the exact triggering path (how many concurrently outstanding bids a single builder can have and whether balance is reserved at bid time) is not fully confirmed from the text explored — this is a genuine uncertainty that should be verified against `process_execution_payload_bid` and `can_builder_cover_bid` in `specs/gloas/beacon-chain.md`. What is concretely proven is the mismatch itself in `get_builder_withdrawals`/`apply_withdrawals`, and the fact that the existing test suite exercises and accepts, without objection, a scenario where `withdrawal.amount` (uncapped) diverges from the debited builder balance.

### Recommendation
Cap the amount placed into the `Withdrawal` object in `get_builder_withdrawals` to the builder's current balance (`amount=min(withdrawal.amount, state.builders[builder_index].balance)`), mirroring how validator sweep/partial withdrawals always derive their `Withdrawal.amount` from the live balance rather than a stale requested value. This makes the amount committed to the execution layer identical to what `apply_withdrawals` actually debits, restoring the withdraw ↔ debit equality.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH`.
2. Through the bid/payment-settlement flow (`apply_parent_execution_payload` → `settle_builder_payment`, or the direct-append fallback path), a `BuilderPendingWithdrawal(builder_index=B, amount=5 ETH, fee_recipient=F)` is appended to `state.builder_pending_withdrawals` (this reflects committed bid value from a prior period; see `specs/gloas/beacon-chain.md:1518-1527` and `1761-1770`).
3. `process_withdrawals` runs; `get_builder_withdrawals` emits `Withdrawal(validator_index=convert(B), address=F, amount=5 ETH)` unconditionally (`specs/gloas/beacon-chain.md:1822-1829`).
4. This withdrawal is committed into `state.payload_expected_withdrawals`, binding the execution layer to credit `F` with `5 ETH`.
5. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)` → `balance = 0` (`specs/gloas/beacon-chain.md:1926-1929`).
6. Net effect: `F` is credited `5 ETH` on the execution layer; only `1 ETH` was ever debited from any consensus-layer account. `4 ETH` has been created from nothing — exactly reproduced by the existing unit test `test_builder_withdrawal_insufficient_balance` (`tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:114-156`), which asserts `withdrawal_amounts_builders={builder_index: withdrawal_amount}` (the uncapped 5 ETH) alongside `builder_balances={builder_index: 0}`.

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

**File:** specs/electra/beacon-chain.md (L1381-1393)
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
```

**File:** specs/capella/beacon-chain.md (L447-468)
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
                    amount=balance - MAX_EFFECTIVE_BALANCE,
                )
            )
            withdrawal_index += 1
```
