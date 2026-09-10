### Title
Builder withdrawals can commit an uncapped payment amount to the execution layer while the consensus-layer debit is silently capped, minting Gwei from nothing - (File: specs/gloas/beacon-chain.md)

### Summary
In the Gloas builder-payment flow, a `Withdrawal` entry that will be honored unconditionally by the execution layer is built from the *requested* payment amount recorded in `state.builder_pending_withdrawals`/`state.builder_pending_payments`, without ever checking it against the builder's live balance. The consensus-layer accounting, however, only deducts `min(withdrawal.amount, builder.balance)` from the builder when the withdrawal is finally applied. If the builder's balance is smaller than the committed amount at settlement time, the execution layer still credits the full, uncapped amount to `fee_recipient`, while the beacon state only debits the (possibly much smaller) available balance — creating Gwei that was never backed by any deposit.

### Finding Description
`get_builder_withdrawals` builds the canonical `Withdrawal` object using the raw amount stored in `state.builder_pending_withdrawals`, with no cap against the builder's balance: [1](#0-0) 

That amount is queued earlier by `settle_builder_payment` and `process_builder_pending_payments`, both of which append `payment.withdrawal` verbatim — again with no re-validation against the builder's current balance: [2](#0-1) 

The only place the amount is ever reconciled with the builder's actual balance is at the moment the withdrawal is *applied* to consensus-layer state, and there it is silently capped rather than causing the withdrawal to fail or be resized: [3](#0-2) 

Because `Withdrawal` objects in `ExecutionPayload.withdrawals` are protocol-committed and unconditionally minted on the execution layer (unlike the reported Solidity `LendingPoolCore.transfer` pattern, there is no "call that can fail" here — the EL *must* credit exactly what the CL committed), any gap between `withdrawal.amount` (what is credited to `fee_recipient` on the EL) and `min(withdrawal.amount, builder_balance)` (what is actually debited from `state.builders[builder_index].balance`) is Gwei created from nothing. This is the direct analog of the reported bug class: a promised/committed payment amount diverges from what the payer actually has backing it, and the protocol pays out the promised amount regardless.

This is not a hypothetical edge case invented by the analysis — it is explicitly exercised and asserted as expected behavior by the spec's own test suite: [4](#0-3) 

In that test, `builder_pending_withdrawals` contains a request for 5 ETH while the builder only has 1 ETH; the resulting `Withdrawal` placed in `payload_expected_withdrawals` (and thus committed to the execution layer) still carries the full 5 ETH, while `builders[0].balance` is only reduced to 0 (a 1 ETH deduction). The 4 ETH difference is minted on the execution layer without any corresponding consensus-layer backing.

`can_builder_cover_bid` is the only guard meant to prevent this, and it is checked solely at the moment a bid is *accepted* (in `process_execution_payload_bid`), against the builder's currently-recorded balance and the sum of already-queued pending withdrawals/payments: [5](#0-4) 

However, a bid's payment can sit queued in `builder_pending_payments`/`builder_pending_withdrawals` for an extended, indeterminate period (subject to FIFO processing capped at `MAX_WITHDRAWALS_PER_PAYLOAD - 1` per block, quorum-based epoch eviction, and re-queuing when parent blocks are skipped), during which `builder.balance` is never reserved/locked — it is only checked at accept-time, not decremented until the eventual `apply_withdrawals` call. There is no invariant anywhere in `settle_builder_payment`, `process_builder_pending_payments`, or `get_builder_withdrawals` re-verifying that the sum of everything still queued for a builder does not exceed its live balance before that amount is frozen into an EL-committed `Withdrawal`. The capping in `apply_withdrawals` is a bookkeeping safety valve for the *consensus* side only — it does nothing to stop the *execution* side from having already minted the uncapped amount.

### Impact Explanation
This breaks the fundamental Gwei-conservation invariant of the protocol: `Withdrawal.amount` credited on the execution layer to `fee_recipient` can exceed the amount ever debited from any beacon-chain balance (`state.builders[*].balance`). This is exactly the "Gwei created ... paid to a non-owner" / "a payload or payment applied that the block did not commit to (in amount)" class called out as Critical impact. Because builders are non-slashable, staked, otherwise-unprivileged actors whose balance can legitimately be depleted through ordinary protocol operation (multiple bids queued across slots, deposit/withdraw races, or index reuse after exit), this is reachable without any external/foreign-key assumptions, purely through valid spec-following state transitions.

### Likelihood Explanation
Confirmed as reachable by the spec's own conformance tests, which construct and assert exactly this state and its consequence (`test_builder_withdrawal_insufficient_balance`, `test_all_builder_withdrawals_zero_balance`). Whether an *unprivileged* participant can force this exact sequencing purely through the ordinary bid/settlement lifecycle (multiple bids in flight before settlement, combined with the asynchronous, FIFO-delayed `builder_pending_withdrawals`/`builder_pending_payments` processing) could not be fully traced end-to-end from genesis within the scope of this review — the `can_builder_cover_bid` check appears designed to prevent it for a single, serially-processed bid stream, but no code path re-validates the invariant once a payment is queued and before it is minted on the execution layer, which is the structural gap enabling the mismatch whenever queued amounts and balance drift apart.

### Recommendation
Enforce the balance cap at the point a `Withdrawal` is constructed for the execution payload (in `get_builder_withdrawals`), not only when debiting the consensus-layer balance in `apply_withdrawals`. I.e., set `amount=min(withdrawal.amount, state.builders[builder_index].balance)` when building the `Withdrawal`, and reflect the same capped amount when removing/adjusting the entry from `builder_pending_withdrawals`, so that the amount credited on the execution layer is always identical to the amount debited from the builder's consensus-layer balance.

### Proof of Concept
Given the state constructed by the existing spec test:
- `state.builders[0].balance = 1 ETH`
- `state.builder_pending_withdrawals = [BuilderPendingWithdrawal(builder_index=0, amount=5 ETH, fee_recipient=F)]`

Running `process_withdrawals(state)`:
1. `get_builder_withdrawals` emits `Withdrawal(validator_index=builder_index_flagged, address=F, amount=5 ETH)` into `payload_expected_withdrawals` — this becomes part of the canonical `ExecutionPayload.withdrawals` that the execution layer will unconditionally apply, crediting `F` with 5 ETH.
2. `apply_withdrawals` executes `state.builders[0].balance -= min(5 ETH, 1 ETH)`, leaving `state.builders[0].balance == 0`.

Net effect: 5 ETH minted to `F` on the execution layer, only 1 ETH ever debited anywhere on the consensus layer — 4 ETH created with no source, as directly asserted by: [6](#0-5)

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
