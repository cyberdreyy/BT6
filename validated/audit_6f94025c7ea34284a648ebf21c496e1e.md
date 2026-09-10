### Title
Builder pending withdrawal amount can exceed builder balance, crediting the execution-layer withdrawal recipient more Gwei than is deducted from the builder — ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` (new in Gloas/EIP-7732) creates an execution-layer `Withdrawal` whose `amount` field is always the *requested* `withdrawal.amount` taken verbatim from `state.builder_pending_withdrawals`, without ever checking or capping it against the builder's actual balance. The balance deduction happens later, in `apply_withdrawals`, which *does* cap the deduction to `min(withdrawal.amount, builder_balance)`. This mirrors the reported bug class: a required-vs-sent/available check is only a "lower bound" style check (or missing entirely) instead of an exact-match check, so the two sides of the payment diverge.

### Finding Description [1](#0-0) 
`get_builder_withdrawals` iterates `state.builder_pending_withdrawals` and, for each entry, unconditionally builds:
```python
Withdrawal(
    index=withdrawal_index,
    validator_index=convert_builder_index_to_validator_index(builder_index),
    address=withdrawal.fee_recipient,
    amount=withdrawal.amount,
)
```
with no comparison to `state.builders[builder_index].balance`.

Later, `apply_withdrawals` deducts from the builder: [2](#0-1) 
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

So the `Withdrawal` object emitted into `state.payload_expected_withdrawals` — which the execution layer is *required* to honor by crediting `withdrawal.amount` to `withdrawal.address` — carries the **uncapped requested amount**, while the beacon state only ever subtracts `min(withdrawal.amount, builder_balance)` from the builder. If `withdrawal.amount > builder_balance`, the execution-layer credit (`withdrawal.amount`) exceeds the beacon-state debit (`builder_balance`), creating Gwei that was never backed by any account's balance — the amount actually paid does not equal the amount actually committed/deducted, breaking exactly the equality the report's bug class targets ("a payload or payment applied that the block did not commit to" / "Gwei created ... paid to a non-owner").

This is confirmed by the repo's own tests, which explicitly document this as the intended (though value-breaking) behavior: [3](#0-2) 
```
Input:
    - state.builders[0]: Builder exists with only 1 ETH balance
    - builder_pending_withdrawals: Contains 1 entry requesting 5 ETH
Output:
    - withdrawal.amount: 5 ETH (requested amount)
    - builders[0].balance: 0 (deduction capped to available balance)
```
i.e. the withdrawal instructs the EL to pay out 5 ETH to `fee_recipient`, while only 1 ETH was ever removed from the builder's beacon-state balance — a 4 ETH mismatch between commitment and payment.

The spec's own commentary elsewhere emphasizes that withdrawal deductions must be applied immediately specifically to preserve the "total supply invariant" [4](#0-3) , yet this uncapped-amount/capped-deduction pattern directly violates that same invariant when a builder's pending withdrawal request exceeds its balance.

### Impact Explanation
This falls under Critical impact: "Gwei created ... paid to a non-owner" and "a payload executed or paid that the block did not commit to." The execution layer is contractually bound to pay the full `withdrawal.amount` from `payload_expected_withdrawals` (this is a core consensus/EL coupling — mismatched withdrawal application is a state-transition-invalidating condition). If the beacon chain only debited a smaller amount from the builder, the excess payment amount has no backing balance anywhere in the beacon state — it is minted out of thin air and paid to `fee_recipient`, an address the protocol did not fully fund.

### Likelihood Explanation
Reaching `builder.balance < withdrawal.amount` requires a `BuilderPendingWithdrawal` entry with `amount` greater than the builder's current balance. Nothing in `process_execution_payload_bid` (which enqueues `BuilderPendingPayment`/`BuilderPendingWithdrawal` entries) or in the queue-processing path appears to strictly guarantee `withdrawal.amount <= builder.balance` at the time `get_builder_withdrawals` runs — a builder's balance can be reduced by other slashable/withdrawal events between when a pending payment is queued and when it is turned into a withdrawal, or an implementation bug/edge case in payment-queue bookkeeping could allow `amount` to exceed balance. Given the repo's own tests deliberately construct and assert this exact scenario as "expected" behavior, this is a directly reachable code path with no attacker coordination required — likelihood is High to Critical, contingent only on confirming there is no additional invariant elsewhere in the builder-payment lifecycle that always keeps `amount <= balance` (which I was not able to fully verify from `process_execution_payload_bid` / `process_builder_pending_payments` in the time available — see caveat below).

### Recommendation
Cap `withdrawal.amount` in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` at withdrawal-construction time, so the amount instructed for execution-layer payment always matches the amount actually deducted in `apply_withdrawals`. Alternatively, enforce (and prove) an invariant elsewhere in the spec that a `BuilderPendingWithdrawal.amount` can never exceed the builder's balance at the time it's converted into a withdrawal.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH`.
2. `state.builder_pending_withdrawals` contains an entry `{builder_index: B, fee_recipient: X, amount: 5 ETH}` (as constructed by the repo's own test `test_builder_withdrawal_insufficient_balance` [5](#0-4) ).
3. `process_withdrawals` → `get_expected_withdrawals` → `get_builder_withdrawals` emits `Withdrawal(address=X, amount=5 ETH)` into `state.payload_expected_withdrawals`.
4. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)` → builder balance becomes `0`.
5. The execution layer, per the withdrawals commitment, credits `X` with `5 ETH`, while only `1 ETH` was ever removed from any beacon-state balance — `4 ETH` is created without provenance.

**Caveat / uncertainty:** I could not fully trace, within the available search budget, whether `process_execution_payload_bid`/the builder-pending-payment quorum logic in `specs/gloas/beacon-chain.md` guarantees `amount <= builder.balance` is maintained at the moment the pending withdrawal is created, which would determine whether this state is actually reachable via normal (non-buggy) protocol operation versus only via an already-inconsistent state. The repo's own test suite treats "requested amount > available balance" as a valid, expected input to `process_withdrawals`, which supports that this is a reachable and spec-sanctioned code path rather than a defensive/unreachable branch.

### Citations

**File:** specs/gloas/beacon-chain.md (L1804-1834)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1976-1982)
```markdown

*Note*: Unlike deposits (which are applied at the child's slot via
`apply_parent_execution_payload`), withdrawal balance deductions are applied
immediately via `apply_withdrawals`. Deferring the deduction to the child's slot
would break the total supply invariant: state transitions between the commitment
slot and the deduction slot (e.g., `process_pending_consolidations` at an epoch
boundary) can reduce a validator's balance below the committed withdrawal
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
