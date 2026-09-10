### Title
Slashing a proposer permanently destroys the builder payment owed for that proposer's already-committed, faultless block - ([File: specs/gloas/beacon-chain.md])

### Summary
`process_proposer_slashing` in `specs/gloas/beacon-chain.md` deletes the `BuilderPendingPayment` entry associated with a slashed proposer's slot whenever that proposer is the recorded `proposer_index` for the payment, without any check on whether the specific block that earned the payment (the one actually canonicalized and whose execution payload/bid was processed) was itself invalid. This destroys Gwei owed to an unrelated, non-equivocating builder who already fulfilled their bid.

### Finding Description
When a block's execution-payload bid carries a non-zero `value`, `process_execution_payload_bid` records a `BuilderPendingPayment` keyed by slot, including the actual proposer of that slot (`get_beacon_proposer_index(state)`): [1](#0-0) 

This payment is meant to eventually become a `BuilderPendingWithdrawal` paid to the builder's `fee_recipient`, either via `settle_builder_payment` when the child block processes the parent payload, or via `process_builder_pending_payments` if PTC-attestation weight clears quorum: [2](#0-1) 

`process_proposer_slashing`, however, unconditionally wipes this pending payment to `BuilderPendingPayment.empty()` (destroying it - not moving it anywhere) as soon as it finds `payment.proposer_index == header_1.proposer_index`, i.e. whenever the *same* validator who proposed the slot in question is later found to be slashable for equivocation: [3](#0-2) 

The equivocation evidence (`header_1`/`header_2`) only proves the proposer signed two conflicting block headers for that slot - it says nothing about whether the *builder's* payload/bid, which was separately verified via BLS signature and committed on-chain in `process_execution_payload_bid`, was itself faulty. The builder is a distinct economic actor (their own registry, balance, and payment recipient) from the proposer, and did nothing wrong. Yet the code's own comment shows the authors were alert to griefing at the payment-index level ("otherwise an unrelated same-slot equivocation could grief an honest proposer's payment") but only guarded against *index-collision* griefing (a different proposer's payment landing at the same index) - it did not add any check preserving the recorded builder's payment when the associated proposer is slashed for unrelated equivocation.

### Impact Explanation
This breaks the equality that a Gwei legitimately earned by a non-faulty party (the builder, who fulfilled a valid, signed, funds-covered bid and had it canonically included) must either be paid to that party or remain untouched. Instead the amount is deleted from state entirely - neither paid to the builder nor returned to the proposer/protocol. Per the report's impact categories this is "Gwei ... destroyed," which the task rules classify as Critical impact. A single validator's equivocation (their own fault, unrelated to the builder) can unilaterally erase another honest participant's (the builder's) earned payment, i.e. "a builder payment ... misdirected" in the sense that it never reaches its rightful recipient.

### Likelihood Explanation
Triggering this requires only that: (1) a proposer for slot S includes a bid with `value > 0` from an honest builder, which gets recorded as a pending payment with `proposer_index = S`'s proposer, and (2) any party later submits a valid `ProposerSlashing` for that same proposer/slot within the current or previous epoch (before the payment is otherwise settled by the very next block or PTC quorum). Equivocation by a proposer is not a contrived edge case - it can happen from validator client misconfiguration, failover setups running duplicate keys, or a proposer deliberately signing a second header. No coalition, foreign key, or client bug is needed - any single validator, honest or not, that ends up equivocating for a slot where they accepted a paid builder bid, and any third party who then submits that slashing, extinguishes the builder's payment. This is squarely within the unprivileged-participant, single-actor reachability bar the task requires.

### Recommendation
Do not delete the recorded `BuilderPendingPayment` based solely on proposer identity. Either:
- Always settle (append to `builder_pending_withdrawals`) the pending payment for a slashed proposer's slot instead of discarding it, since the builder's obligation was independently signed/verified and unrelated to the proposer's equivocation; or
- Only clear the payment when the slashing evidence pertains to the specific header that was actually used to produce the pending payment (e.g., verify `header_1` corresponds to the canonical block whose bid created this payment), so that a builder's earned payment is preserved whenever their bid was genuinely included and valid.

### Proof of Concept
1. Proposer `P` is selected for slot `S`. `P` proposes a block including a builder bid from builder `B` with `value = V > 0`, `fee_recipient = B_addr`. `process_execution_payload_bid` records:
   `state.builder_pending_payments[idx] = BuilderPendingPayment(weight=0, withdrawal=BuilderPendingWithdrawal(fee_recipient=B_addr, amount=V, builder_index=B), proposer_index=P)` [4](#0-3) 
2. `P` also signs a second, conflicting `BeaconBlockHeader` for slot `S` (e.g. due to duplicate-signer misconfiguration or intentionally), producing valid equivocation evidence.
3. Before the pending payment is settled (i.e., before the child block at `S+1` processes `apply_parent_execution_payload`/`settle_builder_payment`, or before PTC weight reaches quorum in `process_builder_pending_payments`), any party constructs a `ProposerSlashing` with `header_1`, `header_2` for slot `S` and includes it in a subsequent block within the same or previous epoch.
4. `process_proposer_slashing` executes: `payment.proposer_index == header_1.proposer_index` is true (both equal `P`), so `state.builder_pending_payments[idx] = BuilderPendingPayment.empty()` [5](#0-4) .
5. Result: `V` Gwei that builder `B` had legitimately earned for a valid, already-canonicalized payload is permanently erased from state - never appended to `builder_pending_withdrawals`, never paid to `B_addr`, and not returned to `P` or the protocol. `state.balances`/`state.builders[B].balance` show no corresponding credit anywhere - the Gwei is destroyed, breaking the invariant that a valid builder payment committed on-chain must eventually be paid to its designated recipient.

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

**File:** specs/gloas/beacon-chain.md (L2124-2140)
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

**File:** specs/gloas/beacon-chain.md (L2451-2467)
```markdown
    # [New in Gloas:EIP7732]
    # Remove the BuilderPendingPayment corresponding to this proposal if it is
    # still in the 2-epoch window. Only clear it when the slashed validator is
    # the proposer associated with the payment; otherwise an unrelated same-slot
    # equivocation could grief an honest proposer's payment.
    slot = header_1.slot
    proposal_epoch = compute_epoch_at_slot(slot)
    if proposal_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + slot % SLOTS_PER_EPOCH
        payment = state.builder_pending_payments[payment_index]
        if payment.proposer_index == header_1.proposer_index:
            state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
    elif proposal_epoch == get_previous_epoch(state):
        payment_index = slot % SLOTS_PER_EPOCH
        payment = state.builder_pending_payments[payment_index]
        if payment.proposer_index == header_1.proposer_index:
            state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```
