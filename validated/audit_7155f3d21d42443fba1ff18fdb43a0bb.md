### Title
Builder withdrawal payments can mint Gwei that the beacon state never subtracts, because the committed `Withdrawal.amount` is not capped to the builder's actual balance - (File: `specs/gloas/beacon-chain.md`)

### Summary
In the Gloas builder-payment design, `apply_withdrawals` caps the beacon-state balance deduction for a builder withdrawal to `min(withdrawal.amount, builder_balance)`, but the `Withdrawal.amount` field that is placed into `state.payload_expected_withdrawals` — and which the execution layer is required to honor verbatim — is never capped. This reproduces the "fee on transfer" class of bug from the referenced report: the amount the protocol *commits to pay out* diverges from the amount it actually *debits internally*, so a payment can be executed for more value than was ever removed from any balance.

### Finding Description
`apply_withdrawals` in `specs/gloas/beacon-chain.md` is: [1](#0-0) 

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

The `withdrawal.amount` value used here is the value already baked into the `Withdrawal` object that was placed in `state.payload_expected_withdrawals` earlier in `process_withdrawals` (via `get_expected_withdrawals` → the builder pending-withdrawal / builder-sweep helpers). That amount is the **requested** amount from `builder_pending_withdrawals[i].amount`, not the amount actually available. This is explicitly documented in the input/output analysis of the function: [2](#0-1) 

> `.amount` | `Gwei` | `Uint64`. Requested amount. **Actual = `min(amount, builder.balance)`**.

Per the spec's own note on `process_withdrawals`, `state.payload_expected_withdrawals` is not just internal bookkeeping — it is the authoritative set of withdrawals that "any execution payload that has the corresponding block as parent beacon block is required to honor ... in the execution layer": [3](#0-2) 

So the execution layer will credit the withdrawal's destination address with the **full, uncapped `withdrawal.amount`**, while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from `state.builders[...].balance`. When `withdrawal.amount > builder_balance`, the difference is Gwei that is paid out on the EL side but never subtracted from any CL balance — it is created from nothing.

This is confirmed by the project's own tests, which explicitly assert this exact behavior as "correct": [4](#0-3) 

`test_builder_withdrawal_insufficient_balance` sets `withdrawal_amount = 5 ETH` against `available_balance = 1 ETH`, then asserts `builder_balances={builder_index: 0}` (i.e. only 1 ETH was ever subtracted) while `withdrawal_amounts_builders={builder_index: withdrawal_amount}` still records the withdrawal as 5 ETH — the value that per the spec note must be honored on the EL side.

### Impact Explanation
This breaks the "Gwei created and paid to a non-owner" invariant: the execution layer would mint/transfer the full requested amount to the withdrawal's `fee_recipient`/`execution_address`, while the consensus layer's builder balance is reduced by a strictly smaller (capped) amount. Every builder pending-withdrawal or sweep withdrawal whose requested amount exceeds the builder's actual balance at execution time inflates total ETH supply by the shortfall, credited to an address the protocol never verified as entitled to the excess. Under the rules given, this is a Critical-class finding (Gwei created and paid to a non-owner).

### Likelihood Explanation
This condition is reachable whenever a builder's balance drops below a previously queued/committed withdrawal amount before that withdrawal is processed — e.g., multiple builder pending withdrawals stacked against the same builder, or balance reduced by an intervening penalty/slashing/other withdrawal between the time the pending withdrawal amount was queued and the time it is swept in `apply_withdrawals`. No malicious peer, coalition, or client bug is required; it is a direct consequence of normal state evolution combined with the un-capped `Withdrawal.amount` used for the EL-facing commitment.

### Recommendation
`Withdrawal.amount` placed into `state.payload_expected_withdrawals` for builder withdrawals must be capped to the builder's available balance at withdrawal-construction time (i.e., compute `min(requested_amount, builder_balance)` once, and use that same capped value both for the balance deduction in `apply_withdrawals` and for the `Withdrawal` object that the execution layer is required to honor), so the amount committed to the execution layer always equals the amount actually removed from `state.builders[...].balance`.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH`.
2. A `BuilderPendingWithdrawal` for `B` with `amount = 5 ETH` is queued (e.g., via a prior withdrawal request accepted while balance was higher, then balance reduced by another operation before this withdrawal is swept).
3. `get_expected_withdrawals` builds a `Withdrawal(validator_index=B, amount=5 ETH, ...)` and places it in `state.payload_expected_withdrawals`.
4. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)` → balance becomes `0`, only 1 ETH deducted.
5. The execution layer, required to honor `payload_expected_withdrawals` verbatim, credits the withdrawal's destination address with `5 ETH`.
6. Net result: `4 ETH` created out of nothing and paid to the destination address, with no corresponding decrease anywhere in the beacon state. [1](#0-0) [4](#0-3)

### Citations

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

**File:** specs/gloas/beacon-chain.md (L1969-1976)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L33-34)
```markdown
| (in `builder_pending_withdrawals`) `.fee_recipient`      | `ExecutionAddress`                                                  | `Bytes20`. Withdrawal destination. Independent of builder's own `execution_address`.                                                                                 | Withdrawal destination                  |
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
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
