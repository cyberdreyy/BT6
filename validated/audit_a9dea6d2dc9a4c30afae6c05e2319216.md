### Title
Builder withdrawal amount committed to the execution payload can exceed the builder's actual balance deducted — (File: `specs/gloas/beacon-chain.md`)

### Summary
In the Gloas (ePBS) fork, `get_builder_withdrawals` and `get_builders_sweep_withdrawals` construct `Withdrawal` objects whose `amount` is taken directly from `state.builder_pending_withdrawals[*].amount` (or `builder.balance` for the sweep case) and place them into `state.payload_expected_withdrawals` — the value that binds the execution layer to pay that exact amount to the destination address. However, `apply_withdrawals` only decreases the builder's on-chain balance by `min(withdrawal.amount, builder_balance)`. When several pending withdrawals accumulate against the same builder faster than its balance can cover them, the committed (EL-facing) amount and the actual (CL-facing) balance decrease diverge, exactly mirroring the PufferVault bug where a counter (`lidoLockedETH`) was incremented by a requested amount while the real balance change could be smaller.

### Finding Description
`get_builder_withdrawals` builds each `Withdrawal` unconditionally from the queued amount: [1](#0-0) 

`get_builders_sweep_withdrawals` does the same using `builder.balance` at read time: [2](#0-1) 

These withdrawals are appended into `state.payload_expected_withdrawals`, which is the value the execution layer is committed to honor for the corresponding payload (per the `process_withdrawals` note that withdrawals are deterministic given the state and any payload must honor them): [3](#0-2) [4](#0-3) 

But the actual CL-side deduction, performed in `apply_withdrawals`, caps the decrease to whatever balance is left at the moment of application: [5](#0-4) 

`state.builder_pending_withdrawals` is populated by `process_builder_pending_payments`, which appends one withdrawal per slot in the previous epoch whose payment reached quorum, without checking it against the sum of already-queued withdrawals for the same builder: [6](#0-5) 

If a builder wins multiple slots' bids within an epoch (a normal, unprivileged occurrence — no coalition or client bug required), multiple `BuilderPendingWithdrawal` entries can be queued against the same builder whose amounts, summed, exceed the builder's actual balance at the time they are drained in `get_builder_withdrawals`/`apply_withdrawals`. Because `get_builder_withdrawals` copies the queued (uncapped) amount into the committed `Withdrawal`, while `apply_withdrawals` deducts only `min(amount, balance)`, the second (or later) withdrawal in the same block can be committed at its full requested amount even though the builder's remaining balance is already zero or insufficient — the equality "amount promised to be minted by the EL == amount actually removed from CL balance" is broken.

### Impact Explanation
This breaks the invariant that "a Gwei created equals a Gwei destroyed from some account's balance." A committed withdrawal that exceeds the actual decremented builder balance means the execution layer is obligated to pay out ETH that has no corresponding CL-side balance backing — effectively Gwei created and paid to the withdrawal's destination address (`fee_recipient`/`execution_address`) beyond what the protocol's accounting removed from the builder. This matches the "Critical" impact category (Gwei created, destroyed, or paid to a non-owner) from the rules, since it is a genuine supply-invariant break reachable without any malicious peer/coalition — it only requires a builder to win multiple slots within an epoch, a routine, permissionless event.

### Likelihood Explanation
Winning multiple bids within a single epoch is an ordinary, expected outcome for any active, well-performing builder (the spec explicitly allows up to `SLOTS_PER_EPOCH` payments to be promoted per epoch in `process_builder_pending_payments`). No adversarial coordination, timing attack, or client bug is required — only that a builder's balance is insufficient to cover the sum of its multiple committed withdrawal amounts by the time they are drained from `builder_pending_withdrawals` in the same or nearby blocks.

### Recommendation
Enforce that the sum of a builder's outstanding committed withdrawals (all entries in `state.builder_pending_withdrawals` for that builder, plus any sweep withdrawal) never exceeds the builder's current balance at commitment time — either by rejecting/capping the `amount` written into the `Withdrawal` object in `get_builder_withdrawals` to `min(requested_amount, remaining_uncommitted_balance)`, or by tracking a running "reserved" balance per builder that is checked and updated when a payment is promoted to `builder_pending_withdrawals`, ensuring the committed (EL-facing) amount and the balance actually deducted in `apply_withdrawals` can never diverge.

### Proof of Concept
Conceptual state trace (no client access available to run an end-to-end spec test in this environment; described symbolically from the spec text cited above):
1. Builder `B` has `balance = 1 ETH`.
2. Within one epoch, `B` wins two slots' bids, each promising a payment of `1 ETH`; `process_builder_pending_payments` promotes both once quorum is reached, appending two `BuilderPendingWithdrawal(builder_index=B, amount=1 ETH)` entries to `state.builder_pending_withdrawals` — no check against `B.balance` is performed.
3. In a later block, `get_builder_withdrawals` drains both entries (assuming `MAX_WITHDRAWALS_PER_PAYLOAD - 1` allows it), producing two `Withdrawal` objects each with `amount = 1 ETH`, both committed into `state.payload_expected_withdrawals`.
4. `apply_withdrawals` processes them sequentially: the first decreases `B.balance` from `1 ETH` to `0`; the second computes `min(1 ETH, 0) = 0`, so `B.balance` stays `0`.
5. Yet the second `Withdrawal` object committed to the payload still carries `amount = 1 ETH` — the execution layer must credit `1 ETH` to its destination address even though no corresponding `1 ETH` was ever removed from any CL balance for that withdrawal. [7](#0-6) [8](#0-7)

### Citations

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

**File:** specs/gloas/beacon-chain.md (L1858-1868)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1934-1941)
```markdown
##### New `update_payload_expected_withdrawals`

```python
def update_payload_expected_withdrawals(
    state: BeaconState, withdrawals: Sequence[Withdrawal]
) -> None:
    state.payload_expected_withdrawals = Withdrawals(data=withdrawals)
```
```

**File:** specs/gloas/beacon-chain.md (L1969-1990)
```markdown
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
