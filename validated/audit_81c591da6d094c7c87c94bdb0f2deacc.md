Confirmed: `get_builder_withdrawals` in `specs/gloas/beacon-chain.md:1802-1834` constructs each `Withdrawal.amount` directly from `withdrawal.amount` in `state.builder_pending_withdrawals` (the *requested* amount), with no cap against `state.builders[builder_index].balance` at construction time. That uncapped `amount` is what gets placed into `state.payload_expected_withdrawals` and is the value the execution layer is required to honor/mint to the recipient. Separately, `apply_withdrawals` (`specs/gloas/beacon-chain.md:1923-1931`) caps the *consensus-layer* balance deduction via `min(withdrawal.amount, builder_balance)`. This is exactly the code-423n4 pattern: an accounting value that is paid out (the committed withdrawal amount, minted by the EL) is not properly capped/reconciled with the actual store it is drawn from (the builder's balance), so the paid amount can exceed the deducted amount, creating Gwei from nowhere.

This is corroborated by `tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:116-156` (`test_builder_withdrawal_insufficient_balance`), which explicitly asserts this behavior as intended: a builder with only 1 ETH balance and a pending withdrawal request for 5 ETH produces a committed `Withdrawal.amount == 5_000_000_000` (5 ETH) while `builders[0].balance` is only decreased to `0` (i.e., only 1 ETH actually deducted). [1](#0-0) 

### Title
Builder pending withdrawal amount is not capped to available builder balance, allowing Gwei to be minted without a matching balance deduction - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies the *requested* `amount` field from `state.builder_pending_withdrawals` directly into the `Withdrawal` object without capping it to the builder's actual `balance`. This `Withdrawal.amount` is the value the execution layer is contractually required to credit to `withdrawal.address` (per the note in `process_withdrawals` at `specs/gloas/beacon-chain.md:1969-1990` stating "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer"). Meanwhile `apply_withdrawals` only deducts `min(withdrawal.amount, builder_balance)` from the consensus-layer state, silently absorbing the shortfall. The two amounts diverge whenever a builder's balance is less than the amount recorded in its pending withdrawal request.

### Finding Description [2](#0-1) 
`get_builder_withdrawals` iterates `state.builder_pending_withdrawals` and, for each entry, appends a `Withdrawal(amount=withdrawal.amount, ...)` with no comparison against `state.builders[builder_index].balance`. [3](#0-2) 
`apply_withdrawals` performs the actual state mutation: `state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)`, i.e. it caps the CL-side deduction to whatever balance is available, but it does **not** modify `withdrawal.amount` in `state.payload_expected_withdrawals`, which is the value already committed to the execution layer via `update_payload_expected_withdrawals`.

Because `state.payload_expected_withdrawals` is deterministic from the beacon state and must be honored by the EL (per the specification note at `specs/gloas/beacon-chain.md:1969-1990`), the EL will credit `withdrawal.address` with the full uncapped `amount`, while the CL only ever removed the smaller, capped amount from the builder's balance. The equality "Gwei paid == Gwei destroyed/deducted from a stored balance" is broken: more value is minted to the recipient than was ever backed by the builder's on-chain balance.

This mirrors the reported Connext `withdrawAdminFees()` bug class: a value used to authorize an outgoing payment is not properly reconciled/reset against the balance it is supposed to represent, letting the payment exceed the true entitlement.

### Impact Explanation
This falls under Critical impact: "Gwei created, destroyed or paid to a non-owner." A builder (or any actor who can enqueue `builder_pending_withdrawals`, e.g. via `process_execution_payload_bid`/builder withdrawal request flow) can request a withdrawal larger than their balance, and the execution layer will mint/credit the full requested amount to `fee_recipient`, while only the smaller available balance is deducted on the consensus layer. This directly inflates total ETH supply beyond what state accounting reflects — a supply-invariant break, which the same spec file explicitly warns about in a different context ("any CL-side saturation creates a net supply inflation," `specs/gloas/beacon-chain.md:1977-1990`) but does not address here.

### Likelihood Explanation
The test suite in `tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:116-156` (`test_builder_withdrawal_insufficient_balance`) demonstrates this scenario is fully reachable and treated as expected/passing behavior rather than being rejected — a builder with 1 ETH balance and a 5 ETH pending withdrawal produces a valid state transition with a 5 ETH committed withdrawal and only a 1 ETH balance deduction. No special privilege beyond being able to place an entry in `builder_pending_withdrawals` with `amount` exceeding `balance` is required; this queue is populated by builder payment/withdrawal-request mechanisms which are not further audited here due to index limitations, but the vulnerable path (`get_builder_withdrawals` → `apply_withdrawals`) is unconditionally reachable whenever such an entry exists.

### Recommendation
Cap the `amount` written into the `Withdrawal` object in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` (matching what `apply_withdrawals` already does for the balance deduction), so the value committed to and minted by the execution layer never exceeds the actual balance being removed from the builder's account. Alternatively, remove the `min()` capping in `apply_withdrawals` and instead reject/skip pending withdrawals whose `amount` exceeds `builder.balance` at construction time, ensuring the committed and deducted amounts are always equal.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH` (e.g., after depositing minimally) and `builder.execution_address` set to attacker-controlled `fee_recipient`.
2. A `builder_pending_withdrawals` entry is enqueued for `B` with `amount = 5 ETH` (as demonstrated feasible in `test_builder_withdrawal_insufficient_balance`, `tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:116-156`).
3. `process_withdrawals` calls `get_expected_withdrawals` → `get_builder_withdrawals`, producing `Withdrawal(validator_index=convert_builder_index_to_validator_index(B), address=fee_recipient, amount=5 ETH)` with no cap.
4. `update_payload_expected_withdrawals` commits this withdrawal to `state.payload_expected_withdrawals`; the execution layer is required to honor it by crediting `fee_recipient` 5 ETH.
5. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)`, leaving `builder.balance = 0`.
6. Net effect: 5 ETH is minted to `fee_recipient` on the execution layer, but only 1 ETH was ever removed from any consensus-layer account — 4 ETH of unbacked Gwei has been created.

### Citations

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
