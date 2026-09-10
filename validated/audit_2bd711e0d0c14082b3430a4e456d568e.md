## Analysis

I found a strong analog to the Keycloak session-takeover bug: **reusable index identity without invalidation of pending state that references the old identity**. The Keycloak bug is caused by reusing a session id (root session) for a new authentication context without validating that any state bound to the old session still belongs to the original owner. In `consensus-specs`, `BuilderIndex` is explicitly documented as reusable, and a payment queue entry that references a `builder_index` is not re-validated against builder identity before being applied.

### Title
Reused `BuilderIndex` allows a pending builder withdrawal to debit an unrelated new builder's balance - (specs/gloas/beacon-chain.md)

### Summary
`specs/gloas/beacon-chain.md` documents that builder registry slots (`BuilderIndex`) are reusable: once a builder exits and is swept to zero balance, `process_builder_deposit_request` may reassign that same index to a brand-new builder with a different `pubkey`/`execution_address` [1](#0-0) . However, `BuilderPendingWithdrawal` entries queued in `state.builder_pending_withdrawals` only store `builder_index` (plus a `fee_recipient` and `amount`) captured at bid-settlement time, and are processed later, purely by index, with no check that the builder currently occupying that index is still the same builder that earned/owes the withdrawal [2](#0-1) . When applied, `apply_withdrawals` decreases `state.builders[builder_index].balance` for whichever builder currently sits at that index [3](#0-2) .

### Finding Description
The relevant equality that should hold is: *a builder's balance should only be decreased by withdrawals tied to that same builder's own payment/deposit history*. The reuse mechanism breaks this:

1. Builder B (old) submits a bid, wins it; a `BuilderPendingWithdrawal(builder_index=X, fee_recipient=..., amount=V)` is appended to `state.builder_pending_withdrawals` via `settle_builder_payment` / `apply_parent_execution_payload` [4](#0-3) .
2. Before this queue entry is drained (the queue is FIFO-processed at a rate of at most `MAX_WITHDRAWALS_PER_PAYLOAD - 1` per slot, so it can persist across many slots) [5](#0-4) , builder B exits (`process_builder_exit_request` → `initiate_builder_exit`) and is later swept with `balance == 0`, `withdrawable_epoch <= current_epoch`.
3. `process_builder_deposit_request` explicitly permits reusing index X for a *new* builder C with a different pubkey and execution address once `builder.withdrawable_epoch != FAR_FUTURE_EPOCH and builder.balance == 0` [6](#0-5) ; the note at line 2249 confirms this is an intended, known re-assignment behavior, not a bug per se, but the queue-side handling of it is not addressed.
4. Builder C deposits and its `balance` becomes nonzero at index X.
5. The stale `BuilderPendingWithdrawal(builder_index=X, amount=V, fee_recipient=<B's address>)` is eventually processed. `get_builder_withdrawals` converts `builder_index` to a validator index and emits a `Withdrawal` with `address = withdrawal.fee_recipient` (still B's address — so the payment destination is correct) but `apply_withdrawals` decreases `state.builders[X].balance` — which is now **builder C's balance**, not B's [7](#0-6) .

The net effect: builder C's freshly deposited stake is silently debited to pay off a debt/withdrawal that was owed by a completely different (already-exited) builder B. This is a Gwei-accounting violation where value is drained from an unrelated, non-consenting party — directly analogous to the Keycloak flaw where a stale "root session" identifier issued a refresh token/resource to the wrong (new) user because the identifier was reused without validating continuity of ownership.

### Impact Explanation
This falls under the "Critical" bucket: **Gwei destroyed/paid to a non-owner**. A new builder's balance can be reduced by an amount it never owed, effectively transferring funds away from it without its authorization — a direct balance/ownership violation of the "Gwei ... paid to a non-owner" equality this scan is meant to catch.

### Likelihood Explanation
The precondition (an old builder fully sweeping to zero balance while it still has an unprocessed `BuilderPendingWithdrawal` entry, followed by an index reuse via a normal deposit) is reachable through entirely spec-legal operations: normal bid settlement, normal builder exit/sweep, and a normal deposit for a new pubkey. It does not require a client bug, a malicious peer, BLS/KZG break, or majority stake — only ordinary protocol usage timed across a window where the pending-withdrawal queue has not yet drained for that builder. The main uncertainty is exactly how large this timing window is in practice (`builder_pending_withdrawals` queue depth vs. sweep speed), which I could not fully quantify from the available spec text alone.

### Recommendation
Either (a) prevent index reuse while any `builder_pending_withdrawals` entry still references that index, or (b) store enough identity binding (e.g., the builder's `pubkey` or a monotonically increasing generation/session counter alongside `builder_index`) in `BuilderPendingWithdrawal` and validate it against the current occupant of that index before applying the balance deduction in `apply_withdrawals`, skipping/redirecting the debit if the occupant has changed.

### Proof of Concept
1. Builder B at index X wins a bid with value V; `settle_builder_payment`/`apply_parent_execution_payload` appends `BuilderPendingWithdrawal(builder_index=X, fee_recipient=B_addr, amount=V)`.
2. Builder B submits `BuilderExitRequest`; `initiate_builder_exit` sets `withdrawable_epoch`.
3. Builder B is swept via `get_builders_sweep_withdrawals`/`apply_withdrawals`, its `balance` becomes 0, while the earlier pending withdrawal entry for `builder_index=X` is still queued (queue processing rate-limited).
4. A `BuilderDepositRequest` for a brand-new pubkey C arrives; `process_builder_deposit_request` sees `builder.withdrawable_epoch != FAR_FUTURE_EPOCH and builder.balance == 0` at index X and reuses that slot for builder C, resetting `withdrawable_epoch` and setting `balance = deposit_amount`.
5. On a subsequent slot, `get_builder_withdrawals` drains the still-queued `BuilderPendingWithdrawal(builder_index=X, amount=V, fee_recipient=B_addr)`; `apply_withdrawals` executes `state.builders[X].balance -= min(V, state.builders[X].balance)`, debiting builder C's freshly deposited balance to pay out builder B's old obligation. [1](#0-0) [3](#0-2)

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

**File:** specs/gloas/beacon-chain.md (L1804-1932)
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

##### New `get_builders_sweep_withdrawals`

```python
def get_builders_sweep_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    epoch = get_current_epoch(state)
    builders_limit = min(len(state.builders), MAX_BUILDERS_PER_WITHDRAWALS_SWEEP)
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
    builder_index = state.next_withdrawal_builder_index
    for _ in range(builders_limit):
        all_withdrawals = list(prior_withdrawals) + withdrawals
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if has_reached_limit:
            break

        builder = state.builders[builder_index]
        if builder.withdrawable_epoch <= epoch and builder.balance > 0:
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=convert_builder_index_to_validator_index(builder_index),
                    address=builder.execution_address,
                    amount=builder.balance,
                )
            )
            withdrawal_index += 1

        builder_index = (builder_index + 1) % len(state.builders)
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```

##### Modified `get_expected_withdrawals`

```python
def get_expected_withdrawals(state: BeaconState) -> ExpectedWithdrawals:
    withdrawal_index = state.next_withdrawal_index
    withdrawals: list[Withdrawal] = []

    # [New in Gloas:EIP7732]
    # Get builder withdrawals
    builder_withdrawals, withdrawal_index, processed_builder_withdrawals_count = (
        get_builder_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(builder_withdrawals)

    # Get partial withdrawals
    partial_withdrawals, withdrawal_index, processed_partial_withdrawals_count = (
        get_pending_partial_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(partial_withdrawals)

    # [New in Gloas:EIP7732]
    # Get builders sweep withdrawals
    builders_sweep_withdrawals, withdrawal_index, processed_builders_sweep_count = (
        get_builders_sweep_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(builders_sweep_withdrawals)

    # Get validators sweep withdrawals
    validators_sweep_withdrawals, withdrawal_index, processed_validators_sweep_count = (
        get_validators_sweep_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(validators_sweep_withdrawals)

    return ExpectedWithdrawals(
        withdrawals,
        # [New in Gloas:EIP7732]
        processed_builder_withdrawals_count,
        processed_partial_withdrawals_count,
        # [New in Gloas:EIP7732]
        processed_builders_sweep_count,
        processed_validators_sweep_count,
    )
```

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

**File:** specs/gloas/beacon-chain.md (L2247-2254)
```markdown
###### New `process_builder_deposit_request`

*Note*: Builder indices are reusable. When a builder exits, its index may later
be reassigned to a different builder with a new public key. Any deposit sent to
an exited builder will be withdrawn to the builder’s execution address. Exited
builders cannot be reactivated, although a newly registered builder’s public key
may have previously appeared in the builder set. Implementations that rely on
caching should account for this behavior.
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
