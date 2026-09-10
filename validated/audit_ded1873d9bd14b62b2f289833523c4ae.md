### Title
Builder pending withdrawals mint the full requested amount on the EL while the CL only debits the capped balance - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` in `specs/gloas/beacon-chain.md` builds each `Withdrawal` object using the *requested* `BuilderPendingWithdrawal.amount` verbatim, without capping it to the builder's actual `balance`. That uncapped `Withdrawal` is what is committed in `payload_expected_withdrawals` (and thus what the execution layer is required to mint to the withdrawal address), while `apply_withdrawals` only debits `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`. When the requested amount exceeds the builder's balance, the CL-committed/EL-minted amount and the CL-side balance decrease diverge, creating Gwei that is not backed by any real balance decrease — mirroring the reported "price vs. underlying rate" mismatch pattern (using an uncapped/aggregate value instead of the true available value).

### Finding Description
`get_builder_withdrawals` [1](#0-0)  iterates `state.builder_pending_withdrawals` and appends:

```
Withdrawal(
    index=withdrawal_index,
    validator_index=convert_builder_index_to_validator_index(builder_index),
    address=withdrawal.fee_recipient,
    amount=withdrawal.amount,
)
```

There is no `min(withdrawal.amount, state.builders[builder_index].balance)` cap here, unlike the analogous validator-side function `get_pending_partial_withdrawals`, which explicitly caps: `withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)` [2](#0-1) , and unlike the builder *sweep* withdrawal function, which uses `builder.balance` directly as the amount (inherently capped) [3](#0-2) .

The uncapped `Withdrawal` list from `get_builder_withdrawals` flows straight into `get_expected_withdrawals` [4](#0-3)  and becomes `state.payload_expected_withdrawals`, which the execution layer is spec-required to honor exactly: *"any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer"* [5](#0-4) . Meanwhile `apply_withdrawals` only decreases the CL-side `builder.balance` by the capped amount:

```
builder_balance = state.builders[builder_index].balance
state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [6](#0-5) 

So the amount minted by the EL (`withdrawal.amount`, uncapped) and the amount debited from CL state (`min(withdrawal.amount, balance)`, capped) can differ. Any shortfall (`requested - actual balance`) is Gwei created out of thin air and paid to `withdrawal.fee_recipient` — a non-owner value transfer with no matching decrease anywhere in `state`.

The project's own test explicitly documents and asserts this exact behavior as expected: withdrawal amount stays at the full requested 5 ETH while the builder balance is only reduced by the available 1 ETH [7](#0-6) , and the same pattern is repeated with `MIN_DEPOSIT_AMOUNT`-scale numbers [8](#0-7) . The accompanying design report also states the "Actual" builder-pending amount is `min(amount, builder.balance)` for *balance-decrease* purposes only [9](#0-8) , confirming the amount actually paid out by the EL (via `payload_expected_withdrawals`) is not similarly capped.

### Impact Explanation
This breaks the equality that every Gwei paid out by the execution layer must correspond to a Gwei removed from beacon-state balances. Whenever a `BuilderPendingWithdrawal.amount` exceeds the builder's current `balance` (e.g., because the builder's balance was reduced after the withdrawal was queued, or multiple queued withdrawals exceed the balance), the EL mints the full requested amount to `fee_recipient` while the CL only debits the smaller, capped balance. This is a Critical-class issue per the rubric: "Gwei created ... paid to a non-owner", since value is minted with no backing decrease in validator/builder balances, silently inflating supply without any protocol authority approving the excess.

### Likelihood Explanation
No malicious peer, coalition, or client bug is required — it is purely a spec-arithmetic inconsistency triggered by an entirely ordinary state sequence: a `BuilderPendingWithdrawal` queued while balance was sufficient, followed by any state transition that reduces `state.builders[builder_index].balance` before the withdrawal is processed (e.g., other withdrawals to the same builder processed earlier in the same or a prior sweep, or any other CL mechanism that reduces builder balance). Given that `get_builder_withdrawals` performs zero balance validation when constructing the committed `Withdrawal`, this is trivially reachable by any block whose builder-pending queue contains an entry whose amount exceeds the current balance.

### Recommendation
Cap the amount used in the committed `Withdrawal` object itself, not just the balance decrease, e.g.:
```python
actual_amount = min(withdrawal.amount, state.builders[builder_index].balance)
withdrawals.append(Withdrawal(..., amount=actual_amount))
```
and account for balance already consumed by earlier entries in the same `builder_pending_withdrawals` batch for the same builder (analogous to `get_balance_after_withdrawals` used for validator sweeps), so that `payload_expected_withdrawals` and the CL-side balance debit always agree.

### Proof of Concept
1. Builder `b` has `balance = 1 ETH`.
2. `state.builder_pending_withdrawals` contains an entry `{builder_index: b, amount: 5 ETH, fee_recipient: X}`.
3. `get_builder_withdrawals` emits `Withdrawal(validator_index=b, address=X, amount=5 ETH)` unchanged [10](#0-9) .
4. This becomes part of `state.payload_expected_withdrawals`, which the EL must honor by paying `X` exactly `5 ETH` [5](#0-4) .
5. `apply_withdrawals` reduces `state.builders[b].balance` by `min(5 ETH, 1 ETH) = 1 ETH`, leaving it at `0` [6](#0-5) .
6. Net effect: `X` receives `5 ETH` on the EL side; only `1 ETH` was ever removed from CL state — `4 ETH` of Gwei was created from nothing, exactly as confirmed by the existing test's documented expected output [11](#0-10) .

### Citations

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

**File:** specs/gloas/beacon-chain.md (L1858-1867)
```markdown
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
```

**File:** specs/gloas/beacon-chain.md (L1879-1901)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1967-1976)
```markdown
##### Modified `process_withdrawals`

*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L159-206)
```python
@with_gloas_and_later
@spec_state_test
def test_builder_withdrawal_insufficient_balance_realistic_bounds(spec, state):
    """
    Test builder withdrawal with insufficient balance using realistic bounds.

    This test uses MIN_DEPOSIT_AMOUNT-based values to test edge cases with
    realistic builder balances (builders need MIN_DEPOSIT_AMOUNT to be active).

    Input State Configured:
        - state.builders[0]: Builder with balance = MIN_DEPOSIT_AMOUNT + 122 Gwei
        - builder_pending_withdrawals: Contains 1 entry requesting MIN_DEPOSIT_AMOUNT + 123 Gwei
        - builders[0].balance: Insufficient by exactly 1 Gwei

    Output State Verified:
        - payload_expected_withdrawals: Contains 1 withdrawal
        - withdrawal.amount: MIN_DEPOSIT_AMOUNT + 123 Gwei (requested amount)
        - builders[0].balance: 0 (deduction capped to available balance)
        - builder_pending_withdrawals: Reduced by 1 (processed even if capped)
        - next_withdrawal_index: Incremented by 1
    """
    builder_index = 0
    withdrawal_amount = spec.MIN_DEPOSIT_AMOUNT + spec.Gwei(123)
    available_balance = spec.MIN_DEPOSIT_AMOUNT + spec.Gwei(122)

    assert withdrawal_amount > available_balance, "Test requires insufficient balance"

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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L34-34)
```markdown
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```
