### Title
`get_builder_withdrawals` commits the uncapped requested amount to the execution payload while the CL only deducts the available builder balance, minting Gwei the CL never backs - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals`, which builds the `Withdrawal` entries for `state.builder_pending_withdrawals`, sets `amount=withdrawal.amount` — the raw requested amount stored in the queue — without capping it to the builder's actual, currently available balance. This uncapped value becomes part of `state.payload_expected_withdrawals`, which the execution layer is contractually required to honor by minting exactly that amount to `fee_recipient`. Meanwhile, `apply_withdrawals` (the consensus-layer side effect) deducts only `min(withdrawal.amount, builder_balance)` from the builder's balance. When a queued request exceeds the builder's balance, the two sides diverge: the EL mints the full requested amount while the CL removes less (or nothing, if balance is already 0), permanently minting Gwei that no CL balance backs.

### Finding Description
`get_builder_withdrawals` in `specs/gloas/beacon-chain.md` (lines 1802-1834) iterates `state.builder_pending_withdrawals` and appends a `Withdrawal(... amount=withdrawal.amount ...)` for each entry — it never reads `state.builders[builder_index].balance` or clamps the amount: [1](#0-0) 

Contrast this with `get_pending_partial_withdrawals`, which correctly clamps the validator-side analog to the actually available balance before it ever enters the committed withdrawal list: [2](#0-1) 

The uncapped `Withdrawal` list becomes `state.payload_expected_withdrawals` (documented as binding on the EL, which "is required to honor these withdrawals"): [3](#0-2) 

But the CL-side balance mutation caps the deduction to the current balance instead of the committed amount: [4](#0-3) 

This exact asymmetry is called out by the spec authors themselves as a supply-inflation hazard in the surrounding comment, but only in the context of balance drift between commitment and application slot for validator sweep amounts — the same reasoning applies directly here for builder pending withdrawals, except the mismatch can be present from the moment the request enters the queue, not just from timing drift: [5](#0-4) 

This precisely mirrors the reported LOC bug pattern: an amount is computed/queued based on a stale or unchecked "available" value (claimable collateral / builder balance), and a downstream step commits to paying out that stale amount while the actual backing asset (collateral in the vault / builder balance in `state.builders`) has already shrunk — except here, instead of the operation reverting (a DoS), the mismatch is silently absorbed by capping only the CL-side deduction, so the execution layer still mints the full, uncommitted-to-reality amount. This breaks the core Gwei-conservation invariant: Gwei paid out by the EL is not backed by an equal decrease of CL-side balance.

The existing repo test suite already demonstrates the exact scenario end-to-end and treats the divergent output as expected/normal behavior rather than flagging it as a bug: [6](#0-5) 

The test explicitly confirms the committed `Withdrawal.amount` equals the full 5 ETH request while `builders[0].balance` only had 1 ETH and is capped to 0 on the CL side — i.e., the EL will mint 5 ETH but the CL only ever removed 1 ETH worth of stake: [7](#0-6) 

### Impact Explanation
This is a Critical-severity issue under the stated impact categories: it results in "Gwei created ... paid to a non-owner" and "a payload executed or paid that the block did not commit to [match the actual backing]." Specifically, the execution layer mints ETH to `fee_recipient` in excess of what the builder actually staked/owned, inflating total supply with no corresponding CL-side balance decrease. Because `payload_expected_withdrawals` is asserted to equal exactly what the EL must process, every honest client will independently derive and accept the same over-committed withdrawal — this is a protocol-level minting bug, not merely a single node's error, and would be accepted uniformly by all spec-following nodes.

### Likelihood Explanation
`state.builder_pending_withdrawals` entries can be created (via builder payment processing / builder exit flows) with amounts that are not guaranteed at withdrawal-processing time to still be within the builder's current balance — the balance can shrink between when the pending withdrawal amount is recorded and when it is swept via `get_builder_withdrawals`/`apply_withdrawals` (e.g., other withdrawals draining the same builder in the same batch, other builder exits, or subsequent balance changes prior to the sweep). Since `get_builder_withdrawals` performs no balance check at all, any circumstance producing a requested amount greater than the live balance at processing time triggers the mismatch deterministically — this is not a probabilistic or attacker-privileged race but a straightforward consequence of the missing clamp.

### Recommendation
Modify `get_builder_withdrawals` to clamp the committed amount to the builder's actual available balance (mirroring `get_pending_partial_withdrawals`'s pattern), e.g. `amount=min(withdrawal.amount, get_balance_after_prior_withdrawals(state, builder_index, all_withdrawals))`, so the value placed into `payload_expected_withdrawals` (and thus minted by the EL) can never exceed what `apply_withdrawals` actually deducts from `state.builders[builder_index].balance`. This restores parity between the CL-committed payload and the CL-side balance decrease, eliminating the possibility of uncollateralized Gwei minting.

### Proof of Concept
1. Register builder B with balance = 1 ETH.
2. Get a `BuilderPendingWithdrawal` queued for B requesting 5 ETH (e.g., via a pending payment or otherwise reaching `state.builder_pending_withdrawals` with an amount exceeding B's current balance — the existing test harness demonstrates this is directly constructible: [8](#0-7) ).
3. Run `process_withdrawals(state)`.
4. Observe: `state.payload_expected_withdrawals[0].amount == 5 ETH` (committed to the EL, which will mint 5 ETH to `fee_recipient`), while `state.builders[B].balance` only decreases from 1 ETH to 0 ETH.
5. Net result: 4 ETH is minted by the execution layer with no corresponding stake ever held or removed on the consensus layer — a supply-inflation / unauthorized-payment event accepted by every spec-conforming client.

### Citations

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

**File:** tests/core/pyspec/eth_consensus_specs/test/helpers/withdrawals.py (L722-729)
```python
    for builder_index, total_amount in builder_withdrawals.items():
        pre_balance = pre_state.builders[builder_index].balance
        post_balance = state.builders[builder_index].balance
        # Builder withdrawals cap at available balance (spec uses min())
        expected_deduction = min(total_amount, pre_balance)
        assert post_balance == pre_balance - expected_deduction, (
            f"Builder {builder_index} balance must decrease by withdrawal amount"
        )
```
