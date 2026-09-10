Confirmed analog exists: builder deposits/exits/deposit-to-index confusion is guarded by pubkey checks, and `builder_pending_withdrawals[i].builder_index` remains a plain index into `state.builders`, exactly like the tokenId-into-pool confusion in the report — nothing re-validates at settlement time that the builder occupying that index is still the builder that incurred the debt.

### Title
Builder-index reuse lets a stale `BuilderPendingWithdrawal` drain a newly-registered builder's balance - (File: specs/gloas/beacon-chain.md)

### Summary
`process_builder_deposit_request` explicitly documents and implements builder-index reuse: an exited-and-swept builder's slot is handed to a brand-new registrant (`get_index_for_new_builder`, `add_builder_to_registry`) [1](#0-0) . Meanwhile, a `BuilderPendingWithdrawal` created earlier by `process_execution_payload_bid`/`settle_builder_payment` only stores a bare `builder_index`, not the builder's pubkey or a generation counter [2](#0-1) . At withdrawal time, `get_builder_withdrawals`/`apply_withdrawals` resolve that index against whatever builder currently occupies it and debit `state.builders[builder_index].balance` [3](#0-2) [4](#0-3) . This is structurally identical to the reported bug: an unchecked object reference (`tokenId` / here `builder_index`) is trusted to still belong to the same principal it was issued for.

### Finding Description
1. Builder A bids and wins a slot; `process_execution_payload_bid` records a `BuilderPendingPayment`/`BuilderPendingWithdrawal` keyed by `builder_index = i` [5](#0-4) .
2. Before that payment is settled (it can sit for up to ~2 epochs, per `apply_parent_execution_payload`'s payment-index arithmetic [6](#0-5) ), builder A fully exits and is swept to zero balance (`withdrawable_epoch` in the past, `balance == 0`), which is exactly the state `get_index_for_new_builder` looks for to recycle index `i` [7](#0-6) .
3. Any user submits a fresh `BuilderDepositRequest` for a new pubkey; since index `i` is now free, `add_builder_to_registry` places the brand-new builder B at that same index `i` [8](#0-7) .
4. When the stale pending withdrawal for "index `i`" is finally processed (`get_builder_withdrawals` / `apply_withdrawals`), the protocol debits builder B's freshly-deposited balance and pays A's committed `fee_recipient` — B never authorized or benefited from that payment [3](#0-2) [9](#0-8) .

This breaks the equality "a builder's stake is only spent to satisfy that builder's own committed payments": Gwei is destroyed from builder B and paid to a fee_recipient B never committed to, exactly mirroring the smart-contract bug where `decreaseLiquidity()` burned shares belonging to unrelated depositors because it trusted an index/ID without checking that it still referenced the original owner.

### Impact Explanation
This falls under "a builder payment or withdrawal misdirected" (High) — builder B's balance is drained without its authorization to pay a fee_recipient owed by a different, already-exited builder A. In the worst case B's entire top-up deposit could be consumed by a single stale large pending payment, a direct, unauthorized Gwei loss for B with no recourse (the withdrawal is enacted automatically by state transition, not by any action B can block).

### Likelihood Explanation
Requires only ordinary protocol usage, no malicious peer/client bug/coalition: a builder exiting and being swept while it still has a payment in the ~2-epoch settlement pipeline is a normal timing scenario, and index recycling for new deposits is explicit, intended spec behavior. An attacker (or just unlucky ordinary user) depositing right after a slot frees up, combined with any adversarial or opportunistic builder timing its exit around a big pending win, is enough — no elevated privileges or majority stake needed.

### Recommendation
Bind the pending payment/withdrawal to more than a bare index: store (or additionally verify at settlement) the builder's pubkey or a monotonically increasing generation/registration counter alongside `builder_index` in `BuilderPendingPayment`/`BuilderPendingWithdrawal`, and validate it still matches `state.builders[builder_index]` before debiting balance in `get_builder_withdrawals`/`apply_withdrawals`. Alternatively, delay index reuse until all pending payments referencing that index have been settled/evicted.

### Proof of Concept
1. State: `state.builders[i]` = builder A, active, with a bid recorded via `process_execution_payload_bid` producing `BuilderPendingPayment{builder_index=i, withdrawal.amount=V, withdrawal.fee_recipient=A_addr}` [5](#0-4) .
2. Before `settle_builder_payment` runs and before the resulting `BuilderPendingWithdrawal` is drained by `process_withdrawals`, drive `state.builders[i].withdrawable_epoch` into the past and `balance` to 0 (builder A exits and is swept).
3. Submit a `BuilderDepositRequest` for a new pubkey; `get_index_for_new_builder(state)` returns `i` because `builders[i].withdrawable_epoch <= current_epoch and balance == 0` [10](#0-9) ; `state.builders[i]` is overwritten with builder B's data and deposit amount `D` [11](#0-10) .
4. Once the pending withdrawal for index `i` (amount `V`, owed by A) is processed by `get_builder_withdrawals`/`apply_withdrawals`, `state.builders[i].balance` (now B's `D`) is reduced by `min(V, D)` and paid to A's `fee_recipient` [3](#0-2) [9](#0-8)  — B's deposit is misdirected to A's committed recipient.

### Citations

**File:** specs/gloas/beacon-chain.md (L1754-1770)
```markdown
    # Settle the builder payment
    if parent_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_epoch == get_previous_epoch(state):
        payment_index = parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_bid.value > 0:
        # Parent is older than the previous epoch, its payment entry has been
        # evicted from builder_pending_payments. Append the withdrawal directly.
        state.builder_pending_withdrawals.append(
            BuilderPendingWithdrawal(
                fee_recipient=parent_bid.fee_recipient,
                amount=parent_bid.value,
                builder_index=parent_bid.builder_index,
            )
        )
```

**File:** specs/gloas/beacon-chain.md (L1805-1833)
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

**File:** specs/gloas/beacon-chain.md (L2212-2245)
```markdown
###### New `get_index_for_new_builder`

```python
def get_index_for_new_builder(state: BeaconState) -> BuilderIndex:
    for index, builder in enumerate(state.builders):
        if builder.withdrawable_epoch <= get_current_epoch(state) and builder.balance == 0:
            return BuilderIndex(index)
    return BuilderIndex(len(state.builders))
```

###### New `add_builder_to_registry`

```python
def add_builder_to_registry(
    state: BeaconState,
    pubkey: BLSPubkey,
    version: Uint8,
    execution_address: ExecutionAddress,
    amount: Gwei,
    slot: Slot,
) -> None:
    set_or_append_list(
        state.builders,
        get_index_for_new_builder(state),
        Builder(
            pubkey=pubkey,
            version=version,
            execution_address=execution_address,
            balance=amount,
            deposit_epoch=compute_epoch_at_slot(slot),
            withdrawable_epoch=FAR_FUTURE_EPOCH,
        ),
    )
```
```

**File:** specs/gloas/beacon-chain.md (L2256-2284)
```markdown
```python
def process_builder_deposit_request(state: BeaconState, request: BuilderDepositRequest) -> None:
    # Ignore deposits with unexpected withdrawal credential prefixes
    if not is_builder_withdrawal_credential(request.withdrawal_credentials):
        return

    builder_pubkeys = [b.pubkey for b in state.builders]
    if request.pubkey not in builder_pubkeys:
        if is_valid_builder_deposit_signature(request):
            add_builder_to_registry(
                state,
                request.pubkey,
                PAYLOAD_BUILDER_VERSION,
                ExecutionAddress(request.withdrawal_credentials[12:]),
                request.amount,
                state.slot,
            )
    else:
        builder_index = BuilderIndex(builder_pubkeys.index(request.pubkey))
        builder = state.builders[builder_index]

        # If exited and swept, reset the withdrawable epoch
        if builder.withdrawable_epoch != FAR_FUTURE_EPOCH and builder.balance == 0:
            epoch = get_current_epoch(state)
            builder.withdrawable_epoch = epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY

        # Increase balance by deposit amount
        builder.balance += request.amount
```
```
