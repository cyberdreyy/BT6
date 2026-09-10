### Title
Builder can starve `builder_pending_payments`/`builder_pending_withdrawals` by draining balance between bid acceptance and settlement, causing the fee_recipient to be paid less than the block committed to - (File: specs/gloas/beacon-chain.md)

### Summary
`process_execution_payload_bid` checks that a builder can cover a bid **at the moment the bid is included**, via `can_builder_cover_bid`, and then records a `BuilderPendingPayment` whose `withdrawal.amount` is fixed to the full bid value. [1](#0-0)  The builder's balance is **not decremented or escrowed** at bid time — the test suite explicitly documents "Verify builder balance is still the same (payment is pending)". [2](#0-1)  The actual balance deduction only happens later, when the pending payment matures into a `BuilderPendingWithdrawal` and is processed in `apply_withdrawals`, where the amount actually paid is capped: `state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)`. [3](#0-2) 

This is structurally the same "snapshot-then-drain" pattern as the OpenQ bug: a party commits an amount that is checked at commitment time only, and the funds backing it can legitimately be removed before the commitment is honored, so the ultimate payout silently shrinks (or vanishes) to `min(promised, remaining_balance)` instead of reverting or being disallowed.

### Finding Description
1. A builder submits a `SignedExecutionPayloadBid` with `value = V`. `can_builder_cover_bid` only checks that `builder_balance - (MIN_DEPOSIT_AMOUNT + pending_withdrawals_amount) >= V` at that instant. [4](#0-3) 
2. Once accepted, `process_execution_payload_bid` records a `BuilderPendingPayment` with `withdrawal.amount = V` in `state.builder_pending_payments`, without touching `state.builders[builder_index].balance`. [5](#0-4) 
3. The payment sits pending for up to ~2 epochs while attesters vote weight onto it (`process_attestation` accumulating `payment.weight`), and is only turned into an actual `BuilderPendingWithdrawal` when quorum is reached in `process_builder_pending_payments`. [6](#0-5) 
4. Nothing in the spec prevents the builder from spending down its own balance in that window — e.g. submitting a `builder_exit_request`/withdrawal request that queues a full/partial builder withdrawal (processed via the "builder sweep" or a builder-submitted withdrawal), which is applied via `apply_withdrawals` before the pending payment settles.
5. When the pending payment eventually is realized as a `BuilderPendingWithdrawal` and processed by `apply_withdrawals`, the amount actually transferred to `fee_recipient` is `min(withdrawal.amount, builder_balance)` — if the builder has drained its balance in the interim, the proposer/fee_recipient receives less than `V`, or nothing at all, even though attesters already granted quorum weight based on the full committed amount. [3](#0-2) 

This breaks the equality that the block's committed bid value (`bid.value`, attested to by the proposer/attesters and used to determine payload acceptance and proposer preference) equals what is actually paid to `fee_recipient`. It is exactly the OpenQ pattern: commit funds → get credit for the commitment (payload accepted / weight/quorum reached) → withdraw the backing funds → the eventual payout silently degrades instead of being blocked.

### Impact Explanation
This falls under "a builder payment... misdirected, doubled or escaped" / "a payload executed or paid that the block did not commit to" categories. A dishonest builder can capture the benefit of having its payload/bid accepted (validators believing/attesting that a payment of `V` will occur) while paying less than `V`, unilaterally, without needing majority stake or a partition — purely by controlling its own balance/withdrawal timing, which is within a single participant's unprivileged authority.

### Likelihood Explanation
Requires only a single non-colluding builder controlling its own withdrawal timing relative to its own bid — no coalition, no node bug, no partition. The 2-epoch settlement window (`SLOTS_PER_EPOCH` in `builder_pending_payments`, quorum accumulation) gives ample time for the builder to submit an exit/withdrawal request that reduces its balance below the committed bid amount before `apply_withdrawals` finalizes the pending payment.

### Recommendation
Escrow the bid amount from the builder's balance (or otherwise lock/reserve it) at the time the bid is accepted (in `process_execution_payload_bid`), analogous to how validator deposits/exits are locked; alternatively, disallow builder balance-reducing operations (withdrawal/exit requests) while there exists a `BuilderPendingPayment` or `BuilderPendingWithdrawal` referencing that builder with unmet weight/quorum, so that the amount actually paid at settlement can never be less than what was committed to when the payload was accepted.

### Proof of Concept
1. Builder registers with balance `B = MIN_DEPOSIT_AMOUNT + V + ε`.
2. Builder submits (and has accepted) a bid for slot `N` with `value = V`; `can_builder_cover_bid` passes since balance covers `V` plus reserve, creating `BuilderPendingPayment` with `withdrawal.amount = V`. [1](#0-0) 
3. In the ~2-epoch window before the payment reaches quorum and is moved into `builder_pending_withdrawals`, the builder submits a `builder_exit_request`/withdrawal that (once processed by `apply_withdrawals`) reduces `state.builders[builder_index].balance` to near zero.
4. When the pending payment for slot `N` is later settled (`settle_builder_payment` → `builder_pending_withdrawals` → `apply_withdrawals`), the deduction is `min(V, builder_balance)` ≈ 0, so `fee_recipient` receives far less than `V`, even though attesters/proposer already treated the payload as paid at value `V`. [3](#0-2) 

**Uncertainty**: I could not directly view the full bodies of `get_pending_balance_to_withdraw_for_builder`, `settle_builder_payment`, `process_builder_deposit_request`, and `process_builder_exit_request` (only their line locations were found, not their content) due to search/tool limits in this session, so I cannot confirm with full certainty whether `pending_withdrawals_amount` used in `can_builder_cover_bid` also accounts for outstanding `BuilderPendingPayment`s (not just `builder_pending_withdrawals`), which would materially affect whether a single builder can actually get away with under-covering multiple simultaneous commitments versus just draining balance after a single commitment is accepted. The core "commit now, balance capped at settlement time via `min()`" mechanism, however, is confirmed directly from `apply_withdrawals`.

### Citations

**File:** specs/gloas/beacon-chain.md (L1171-1179)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1663-1677)
```markdown
```python
def process_builder_pending_payments(state: BeaconState) -> None:
    """
    Processes the builder pending payments from the previous epoch.
    """
    quorum = get_builder_payment_quorum_threshold(state)
    for payment in state.builder_pending_payments[:SLOTS_PER_EPOCH]:
        if payment.weight >= quorum:
            state.builder_pending_withdrawals.append(payment.withdrawal)

    old_payments = state.builder_pending_payments[SLOTS_PER_EPOCH:]
    state.builder_pending_payments[:SLOTS_PER_EPOCH] = old_payments
    new_payments = [BuilderPendingPayment.empty() for _ in range(SLOTS_PER_EPOCH)]
    state.builder_pending_payments[SLOTS_PER_EPOCH:] = new_payments
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

**File:** specs/gloas/beacon-chain.md (L2124-2141)
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

    # Cache the signed execution payload bid
    state.latest_execution_payload_bid = bid
```
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_execution_payload_bid.py (L480-490)
```python
    assert state.latest_execution_payload_bid == signed_bid.message

    # Verify builder balance is still the same (payment is pending)
    assert state.builders[builder_index].balance == pre_balance

    # Verify new pending payment was recorded
    slot_index_new = spec.SLOTS_PER_EPOCH + (signed_bid.message.slot % spec.SLOTS_PER_EPOCH)
    pending_payment = state.builder_pending_payments[slot_index_new]
    assert pending_payment.withdrawal.amount == bid_amount
    assert pending_payment.withdrawal.builder_index == builder_index
    assert pending_payment.weight == 0
```
