### Title
Reused `builder_index` inherits stale queued withdrawals from a previously exited/swept builder, letting a new builder's deposit fund the old builder's obligations - (File: `specs/gloas/beacon-chain.md`)

### Summary
`state.builders` is a reusable index registry: once a builder's `balance` reaches `0` and its `withdrawable_epoch` has passed, `get_index_for_new_builder` will hand that same `builder_index` to a completely different builder (new pubkey, new `execution_address`) on the next `BuilderDepositRequest`. The check that authorizes reuse only inspects the live `Builder.balance` field; it does not check whether `state.builder_pending_withdrawals` still contains queued entries that reference the same `builder_index`. Because `get_builder_withdrawals`/`apply_withdrawals` resolve the payee purely by `builder_index` (not by pubkey or any generation/nonce tag), a withdrawal queued for the old occupant of the index can later be deducted from the new occupant's balance and paid to the old occupant's fee recipient - this is the same bug class as the reported `AmoManager` issue: replacing/reassigning an authority does not revoke the standing obligation associated with the old authority, so the old commitment silently attaches to whoever now occupies that slot.

### Finding Description
The builder registry recycles slots: [1](#0-0) 

```python
def get_index_for_new_builder(state: BeaconState) -> BuilderIndex:
    for index, builder in enumerate(state.builders):
        if builder.withdrawable_epoch <= get_current_epoch(state) and builder.balance == 0:
            return BuilderIndex(index)
    return BuilderIndex(len(state.builders))
```

and the spec explicitly documents this as intended: [2](#0-1) 

`process_builder_deposit_request` then overwrites the `Builder` container at that index (new `pubkey`, new `execution_address`, fresh `balance`) via `add_builder_to_registry`: [3](#0-2) 

Meanwhile, builder payments are queued into a FIFO list, `state.builder_pending_withdrawals`, tagged only by `builder_index` (not by pubkey or any generation counter): [4](#0-3) 

`get_builder_withdrawals` drains this queue at a limited rate per slot (bounded by `MAX_WITHDRAWALS_PER_PAYLOAD - 1` minus withdrawals already produced this slot), so multiple queued entries for the same `builder_index` can persist across several slots before all being processed: [5](#0-4) 

When a queued item is finally applied, the payout amount is deducted from `state.builders[builder_index].balance` — resolved by index at *application time*, not at *queue time*: [6](#0-5) 

Because `get_index_for_new_builder` only checks the instantaneous `Builder.balance == 0`, it does not check `get_pending_balance_to_withdraw_for_builder(state, index)` (a query that does exist and is used elsewhere, e.g. in `process_builder_exit_request`, but not in the deposit/reuse path). If two (or more) `BuilderPendingWithdrawal` entries exist for the same exited builder — e.g. from two separate settled bids — and the first one drains `balance` to exactly `0` while the second remains queued (because the per-slot processing cap deferred it), the index becomes eligible for reuse *while an obligation for it is still outstanding*. A new builder can then deposit into that same index, and the leftover queued withdrawal will later be deducted from the new builder's freshly deposited balance and paid to the fee recipient that the old builder owed money to.

### Impact Explanation
This breaks the equality that a builder's balance may only be decreased by amounts that builder itself committed to pay (via its own signed bids). Under the scenario above, an honest new builder's deposit is silently confiscated to satisfy a payment obligation created by a completely unrelated, already-exited builder, and that Gwei is paid to a third party (the old builder's owed proposer) that the new builder never authorized. This is a concrete case of "Gwei... paid to a non-owner" / an unauthorized mutation of a builder's balance without its authority, matching the Critical/High impact bar in the rules.

### Likelihood Explanation
Reaching this state requires: (1) a builder accumulating at least two separate settled `BuilderPendingPayment`s that get queued as `BuilderPendingWithdrawal`s referencing the same `builder_index`; (2) the sweep of that queue being rate-limited so the entries aren't all applied atomically in the same slot the balance hits `0` (plausible since the queue is drained incrementally, bounded by `MAX_WITHDRAWALS_PER_PAYLOAD` shared with validator/partial withdrawals and other builder sweep withdrawals); and (3) a new builder submitting a `BuilderDepositRequest` in the intervening window. All of these are ordinary, permissionless, honestly-following-the-spec operations (deposits, bids, exits) — no malicious peer, coalition, or client bug is required, only ordinary chain activity and timing of otherwise valid transactions.

### Recommendation
Do not allow `builder_index` reuse while any obligation against that index remains outstanding. `get_index_for_new_builder` should also require `get_pending_balance_to_withdraw_for_builder(state, index) == 0` (mirroring the check already used in `process_builder_exit_request`) before considering the slot free, e.g.:

```python
def get_index_for_new_builder(state: BeaconState) -> BuilderIndex:
    for index, builder in enumerate(state.builders):
        if (
            builder.withdrawable_epoch <= get_current_epoch(state)
            and builder.balance == 0
            and get_pending_balance_to_withdraw_for_builder(state, BuilderIndex(index)) == 0
        ):
            return BuilderIndex(index)
    return BuilderIndex(len(state.builders))
```

Alternatively, tag `BuilderPendingWithdrawal` entries with the builder's `pubkey` (or a generation/epoch nonce) so that `apply_withdrawals` can verify the withdrawal still targets the same logical builder before debiting the slot's current balance.

### Proof of Concept
1. Builder `B1` occupies `builder_index = I`. Over two slots it accepts two separate winning bids that both settle into `BuilderPendingPayment`s and get appended to `state.builder_pending_withdrawals` as `W1` (amount `A1`, `builder_index=I`) and `W2` (amount `A2`, `builder_index=I`), where `A1` equals `B1`'s remaining `balance` exactly.
2. `B1` submits a `BuilderExitRequest`; `initiate_builder_exit` sets its `withdrawable_epoch`.
3. At the next withdrawal sweep, per-slot throughput caps (`MAX_WITHDRAWALS_PER_PAYLOAD - 1`, shared with other withdrawal categories) cause only `W1` to be processed this slot: `apply_withdrawals` executes `state.builders[I].balance -= min(A1, balance) = A1`, leaving `balance == 0`. `W2` remains queued (`update_builder_pending_withdrawals` only trims the number actually processed).
4. Since `state.builders[I].balance == 0` and `withdrawable_epoch <= current_epoch`, `get_index_for_new_builder` returns `I`. A new builder `B2` submits a `BuilderDepositRequest` with a fresh pubkey/`execution_address`; `add_builder_to_registry` overwrites `state.builders[I]` with `B2`'s data and deposit `amount` (e.g. 32 ETH).
5. On a later slot's withdrawal sweep, `W2` (still `builder_index=I`, still referencing `B1`'s owed fee recipient) is processed: `apply_withdrawals` executes `state.builders[I].balance -= min(A2, balance)`, deducting `A2` from `B2`'s freshly deposited balance and paying it to `B1`'s creditor via `withdrawal.address = W2.fee_recipient`.
6. Result: `B2` loses `A2` Gwei of its own deposit, paid to a party it never dealt with, purely because of stale index reuse — no signature or authorization from `B2` was ever involved in creating `W2`.

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

**File:** specs/gloas/beacon-chain.md (L2212-2220)
```markdown
###### New `get_index_for_new_builder`

```python
def get_index_for_new_builder(state: BeaconState) -> BuilderIndex:
    for index, builder in enumerate(state.builders):
        if builder.withdrawable_epoch <= get_current_epoch(state) and builder.balance == 0:
            return BuilderIndex(index)
    return BuilderIndex(len(state.builders))
```
```

**File:** specs/gloas/beacon-chain.md (L2222-2245)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L2249-2254)
```markdown
*Note*: Builder indices are reusable. When a builder exits, its index may later
be reassigned to a different builder with a new public key. Any deposit sent to
an exited builder will be withdrawn to the builder’s execution address. Exited
builders cannot be reactivated, although a newly registered builder’s public key
may have previously appeared in the builder set. Implementations that rely on
caching should account for this behavior.
```
