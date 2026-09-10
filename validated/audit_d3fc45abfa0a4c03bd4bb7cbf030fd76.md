## Analysis

Confirmed: `get_builder_withdrawals` in `specs/gloas/beacon-chain.md` constructs the committed `Withdrawal.amount` directly from `withdrawal.amount` (the raw `BuilderPendingWithdrawal.amount`, which is simply the builder's bid value recorded back when the bid was accepted), with **no `min()` cap against the builder's current balance**:

```python
withdrawals.append(
    Withdrawal(
        index=withdrawal_index,
        validator_index=convert_builder_index_to_validator_index(builder_index),
        address=withdrawal.fee_recipient,
        amount=withdrawal.amount,
    )
)
``` [1](#0-0) 

But `apply_withdrawals` deducts only the *capped* amount from the builder's internal ledger balance:

```python
if is_builder_index(withdrawal.validator_index):
    builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
    builder_balance = state.builders[builder_index].balance
    state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [2](#0-1) 

The test suite explicitly documents and asserts this exact discrepancy: a builder with 1 ETH balance but a 5 ETH pending withdrawal produces `payload_expected_withdrawals[0].amount == 5 ETH` while `builders[0].balance` only decreases to 0 (a 1 ETH deduction):

```python
Output State Verified:
    - payload_expected_withdrawals: Contains 1 withdrawal
    - withdrawal.amount: 5 ETH (requested amount)
    - builders[0].balance: 0 (deduction capped to available balance)
``` [3](#0-2) 

This is the mirror image of the ERC20 fee-on-transfer bug class: instead of the ledger crediting more than what's actually transferred, here the CL *commits* to the execution layer a withdrawal `amount` (5 ETH) that is larger than what is actually debited from the internal accounting balance (1 ETH). Since `state.payload_expected_withdrawals` is the binding commitment the execution layer must honor exactly (per `process_withdrawals`'s invariant `assert list(payload.withdrawals) == expected.withdrawals` in earlier forks, and the Gloas equivalent enforced via `execution_payload_availability`/bid commitment), the EL will credit the full 5 ETH to `fee_recipient`, while the CL ledger only removed 1 ETH of backing collateral from the builder. This creates 4 ETH of Gwei with no corresponding decrease anywhere in the beacon state — a supply-inflation / value-creation bug, not merely a bookkeeping inconsistency.

### Title
Builder withdrawal amount is not capped to available balance before being committed to the execution layer, allowing Gwei to be created ex nihilo - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` places the raw, uncapped `BuilderPendingWithdrawal.amount` into the `Withdrawal.amount` field that is committed to the execution layer, while `apply_withdrawals` only decreases the builder's CL-tracked balance by `min(withdrawal.amount, builder_balance)`. When a builder's balance is insufficient to cover a pending withdrawal, the amount actually minted/paid on the execution layer exceeds the amount deducted on the consensus layer, creating unbacked Gwei.

### Finding Description
`process_execution_payload_bid` records a `BuilderPendingPayment`/`BuilderPendingWithdrawal` whose `amount` equals the builder's bid value at the time the bid was accepted (`can_builder_cover_bid` is checked then, not at withdrawal time) [4](#0-3) . Between bid acceptance and eventual withdrawal processing, a builder's balance can shrink (e.g., other pending payments settle, other withdrawal sweeps drain the builder, or the builder is charged elsewhere) so that `builder.balance < withdrawal.amount` by the time `get_builder_withdrawals` runs.

`get_builder_withdrawals` copies this stale/uncapped `amount` verbatim into the `Withdrawal` object placed in `state.payload_expected_withdrawals` [1](#0-0) , which is the value the execution layer is contractually bound to pay out (mint) to `fee_recipient`.

Meanwhile `apply_withdrawals`, which mutates the CL-side ledger, caps the deduction: `state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)` [2](#0-1) .

So the equality "Gwei paid out by the EL == Gwei debited from the CL ledger" is broken: the EL mints/pays the full, uncapped `withdrawal.amount`, but the CL only removes `min(withdrawal.amount, builder_balance)` from any account. The difference (`withdrawal.amount - builder_balance`) is new value with no corresponding decrease anywhere in `state.balances` or `state.builders[*].balance`.

### Impact Explanation
This breaks the "Gwei created ... paid to a non-owner" invariant (Critical/High tier per the given impact classes): a `fee_recipient` address controlled by (or colluding with) a builder receives more ETH on the execution layer than was ever removed from the builder's stake on the consensus layer. This is a direct, protocol-level supply-inflation bug reachable purely by an honest-looking sequence of spec-following state transitions (bid → balance depletion via other payments/sweeps → withdrawal processing), not requiring any client bug, malicious peer, or economic-stake attack — it only requires a builder (or anyone who can become a builder and drive its own balance down between bid-time and withdrawal-time) to arrange for its balance to be insufficient when its pending withdrawal is finally processed.

### Likelihood Explanation
The gap between bid-time `can_builder_cover_bid` check and withdrawal-time processing is architected into the design (`BuilderPendingPayment`/`settle_builder_payment` machinery spans multiple slots/epochs), and multiple pending payments/withdrawals plus the builder sweep can all draw down the same `builder.balance` concurrently before a given pending withdrawal is dequeued. A builder can also make additional bids or trigger `initiate_builder_exit`-driven sweep withdrawals to intentionally drain `builder.balance` before its earlier queued pending withdrawal is processed, deliberately engineering the shortfall. The existing test suite already demonstrates and accepts this exact scenario as "expected" behavior (`test_builder_withdrawal_insufficient_balance`), confirming the code path is reachable and produces the mismatch by design, not as a rare edge case.

### Recommendation
Cap the committed `Withdrawal.amount` in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` at the time the withdrawal is scheduled (mirroring the capping already done in `apply_withdrawals`), so the amount actually committed to the execution layer never exceeds what is deducted from `state.builders[*].balance`. Any un-paid remainder of the pending withdrawal should either be dropped or re-queued, but never silently minted on the EL side.

### Proof of Concept
1. Builder `B` bids and its `BuilderPendingWithdrawal` is queued with `amount = 5 ETH` while `B.balance = 6 ETH` (passes `can_builder_cover_bid`) — see `process_execution_payload_bid` [4](#0-3) .
2. Before this withdrawal is dequeued, `B.balance` drops to `1 ETH` (e.g., another pending payment settles, or a builder-sweep withdrawal fires first in the same or an earlier `process_withdrawals` call, since `builder_pending_withdrawals` are FIFO but multiple entries/sweeps can compete for the same balance).
3. `get_builder_withdrawals` still emits `Withdrawal(amount=5 ETH, address=fee_recipient)` into `state.payload_expected_withdrawals` [5](#0-4) .
4. `apply_withdrawals` deducts only `min(5 ETH, 1 ETH) = 1 ETH` from `B.balance`, leaving it at `0` [6](#0-5) .
5. The execution layer, bound by `state.payload_expected_withdrawals`, mints/pays `5 ETH` to `fee_recipient`, while only `1 ETH` was ever debited from any consensus-layer account — a net `4 ETH` of unbacked Gwei has been created and paid to `fee_recipient`. This exact numeric scenario (`5 ETH` requested vs `1 ETH` available, balance capped to `0`) is verified as expected/passing behavior in `test_builder_withdrawal_insufficient_balance` [7](#0-6) .

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
