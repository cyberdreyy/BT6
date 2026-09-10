### Title
Builder withdrawal amount is broadcast to the execution layer uncapped by actual builder balance, allowing Gwei to be created from nothing - (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_builder_withdrawals` emits a `Withdrawal.amount` equal to the *requested* `BuilderPendingWithdrawal.amount`, with no check against the builder's current balance. `apply_withdrawals`, in contrast, deducts only `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`. Because `payload_expected_withdrawals` is the commitment that "any execution payload that has the corresponding block as parent beacon block is required to honor... in the execution layer," the EL will mint/credit the *uncapped* amount to `fee_recipient`, while the CL only ever removes the *capped* amount from the builder's tracked balance. This is the same defect class as the reported Tap.sol bug (a withdrawal ceiling that is silently truncated against available funds) but the consequence here is stronger: instead of merely trapping funds, it manufactures Gwei that no account in the beacon state ever held.

### Finding Description
`get_builder_withdrawals` (called first in `get_expected_withdrawals`) does: [1](#0-0) 
which appends `Withdrawal(..., amount=withdrawal.amount)` for every entry in `state.builder_pending_withdrawals` unconditionally — it never reads `state.builders[builder_index].balance`.

`apply_withdrawals` then does the actual balance deduction: [2](#0-1) 
`state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)`.

If two (or more) `BuilderPendingWithdrawal` entries exist for the same builder whose amounts sum to more than the builder's balance, `get_builder_withdrawals` will still put the *full* uncapped amount for each entry into `payload_expected_withdrawals`, while `apply_withdrawals` will only ever debit the builder down to zero. The equality that should hold — `sum(Withdrawal.amount paid by EL) == sum(balance actually removed from CL)` — is broken. This is a direct analog of the Tap.sol `_maximumWithdrawal` bug: `tapped` (the requested amount) is not properly capped against `balance - minimum` before being committed as an authoritative payout amount; here, `withdrawal.amount` is not capped against `builder.balance` before being committed to the execution layer, so it is not merely truncated (as in the original bug) but paid out in full regardless.

### Impact Explanation
This directly breaks the "Gwei created ... or paid to a non-owner" invariant listed as Critical/High impact: the execution layer would credit `fee_recipient` addresses more total ETH than was ever debited from the corresponding builder(s) in the beacon state, i.e., value minted with no corresponding balance decrease anywhere in `state`. Since `process_withdrawals` treats `payload_expected_withdrawals` as an unconditional commitment the EL must honor, this is not a client-side quirk — it's baked into the spec's state-transition function itself, so all spec-following nodes would agree to the same (incorrect) payload commitment, and the created Gwei would be visible on-chain in EL account balances.

### Likelihood Explanation
Reachability depends on whether it is legitimately possible, without any malicious/coalition action, for a builder to end up with multiple `BuilderPendingWithdrawal` entries whose summed `amount` exceeds `state.builders[builder_index].balance` at the moment `process_withdrawals` runs. `BuilderPendingWithdrawal` entries can be created in at least two ways I confirmed in-repo: via `settle_builder_payment` when a payment reaches quorum, and via the "parent older than previous epoch" fallback in `apply_parent_execution_payload`, which appends `BuilderPendingWithdrawal(amount=parent_bid.value, ...)` directly: [3](#0-2) 
Multiple winning bids by the same builder across consecutive slots can each independently queue a pending withdrawal before any of them are drained by `process_withdrawals`. I located `get_pending_balance_to_withdraw_for_builder`, used by builder-exit-request validation, which suggests the spec is aware that pending withdrawal totals must be tracked against balance — but I was not able to fully confirm within the available search budget whether `can_builder_cover_bid` (checked in `process_execution_payload_bid`) nets out *all* currently-queued-but-unprocessed `builder_pending_withdrawals` amounts (as opposed to only currently pending, not-yet-quorum `builder_pending_payments`) before admitting a new bid. If it does not, multiple bids can be admitted against the same balance before the first is drained, producing exactly the sum-exceeds-balance condition described above. This uncertainty should be resolved by inspecting `can_builder_cover_bid` directly.

### Recommendation
Cap `Withdrawal.amount` in `get_builder_withdrawals` against the builder's balance *net of all already-committed-but-unprocessed pending withdrawals for that builder* (mirroring how `get_pending_partial_withdrawals` caps against `balance - MIN_ACTIVATION_BALANCE`), rather than deferring the cap to `apply_withdrawals`. Concretely, track a running "amount already spoken for" per builder while iterating `state.builder_pending_withdrawals`, and only commit `min(withdrawal.amount, remaining_balance)` as the `Withdrawal.amount` that is actually included in `payload_expected_withdrawals`, so the EL-side commitment can never exceed what `apply_withdrawals` will actually remove from CL state.

### Proof of Concept
1. Builder `B` has `balance = 1.5X` Gwei.
2. Two entries exist in `state.builder_pending_withdrawals` for `B`, each with `amount = X` (e.g., from two separate settled bids queued before either is processed).
3. `get_builder_withdrawals` produces two `Withdrawal` objects, `amount=X` each, total `2X`, inserted into `state.payload_expected_withdrawals`.
4. `process_withdrawals` → `apply_withdrawals`: first withdrawal deducts `min(X, 1.5X) = X` → `B.balance = 0.5X`; second deducts `min(X, 0.5X) = 0.5X` → `B.balance = 0`.
5. Total CL-side deduction = `1.5X` (all of B's balance), but the committed `payload_expected_withdrawals` instructs the EL to pay out `2X` total to the fee recipients — `0.5X` Gwei created with no corresponding source, in violation of total-supply conservation. [4](#0-3) [5](#0-4)

### Citations

**File:** specs/gloas/beacon-chain.md (L1761-1770)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1802-1834)
```markdown
##### New `get_builder_withdrawals`

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
