### Title
Front-running lamport deposit can block full nonce-account withdrawal/closure - (File: `programs/system/src/system_instruction.rs`)

### Summary
`withdraw_nonce_account` decides which authorization/accounting branch to take based on an exact equality check between the *requested withdrawal amount* and the *current account balance*. Because any unprivileged party can increase an account's lamport balance via a normal `SystemInstruction::Transfer` (destination accounts are not restricted to be owned by the sender or to match any particular owner for a lamport-only transfer), an attacker can front-run a nonce authority's "withdraw all and close" transaction by sending a dust amount of lamports to the target nonce account, causing the equality check to fail and the withdrawal to revert.

### Finding Description
`withdraw_nonce_account` in `programs/system/src/system_instruction.rs` handles `State::Initialized`: [1](#0-0) 

When `lamports == from.get_lamports()` the code takes the "full withdraw / close nonce" path, which requires only that the nonce hasn't just advanced this slot and that the authority signed — it does **not** re-check rent-exemption because the account is being fully drained and de-initialized. When the amounts differ (i.e. a *partial* withdrawal, or an inflated/deflated balance), the code instead takes the branch that requires the *remaining* balance to be at least `rent.minimum_balance(...)`.

This is structurally the same bug class as the DYAD `remove()`/`removeKerosene()` issue: an operation is gated on an *exact state comparison* (`Vault(vault).id2asset(id) > 0` in DYAD vs. `lamports == from.get_lamports()` here), and that state is externally, permissionlessly mutable by depositing funds into the target account. A user submits a transaction to withdraw the *entire* current balance of their nonce account (`lamports` = balance observed when building the transaction) in order to fully close it. Before that transaction lands, an attacker sends a `SystemInstruction::Transfer` of 1 lamport to the same nonce account address. Lamport transfers via the System Program only require the *source* to be a system-owned signer account; the *destination* account is not required to be owned by anyone in particular for a lamport-only credit, so the deposit succeeds and the nonce account's balance is bumped by 1 lamport.

When the victim's withdrawal transaction then executes, `lamports` (computed against the stale balance) no longer equals `from.get_lamports()` (now balance+1), so the code falls into the "partial withdraw" branch, which requires `lamports + min_rent_exempt_balance <= from.get_lamports()`. Since the victim asked to withdraw the entire original balance, this check fails and the instruction returns `InstructionError::InsufficientFunds`, reverting the transaction and leaving the nonce account intact and un-closed — exactly like the DYAD `remove()` DoS, where the vault removal is aborted because dust changed the trigger condition.

### Impact Explanation
This lets an unprivileged attacker repeatedly block a legitimate nonce-account owner from closing/fully draining their durable-nonce account by observing pending transactions and front-running with negligible-cost dust transfers. It is a griefing/denial-of-service vector rather than a direct fund theft: the victim's transaction fails (wasting the transaction fee) and the nonce account remains open, requiring the victim to re-derive and resubmit a withdrawal amount that accounts for the new balance, and an adversary willing to keep paying tiny amounts can persistently prevent closure. This does not cause consensus divergence or fund loss to the attacker's benefit, but it is a real state-mutation-blocking griefing primitive reachable purely through ordinary, permissionless transactions (a `Transfer` instruction plus knowledge of the mempool/pending transaction), consistent with the reachable analog classes ("unpriviledged... fee/rent/nonce accounting").

### Likelihood Explanation
Likelihood is low-to-moderate. It requires the attacker to observe the victim's withdrawal transaction before it lands (mempool/QUIC ingest visibility) and to have negligible funds to send a 1-lamport transfer; both are easy for any user. However, the impact is limited to a revert/griefing of a specific "close nonce account with an exact stale balance" pattern rather than fund loss, and most wallets/tools may re-query the balance right before submitting (reducing but not eliminating the race window, since the attacker can still race the final broadcast).

### Recommendation
Avoid gating "full withdraw/close" behavior on an exact equality with the current balance snapshot. Instead, allow closing whenever the requested withdrawal amount is greater than or equal to the account's balance minus rent-exempt minimum (or expose an explicit "close/withdraw-all" semantics that reads the live balance inside the instruction rather than requiring the caller to pre-compute and match it exactly), so that unrelated dust deposits cannot change which authorization/accounting branch is taken.

### Proof of Concept
1. Victim creates and initializes a nonce account with balance `B` under authority `A`, then constructs a `withdraw_nonce_account` transaction with `lamports = B`, `to = A`, intending to fully close the account (per the `lamports == from.get_lamports()` branch at [2](#0-1) ).
2. Attacker observes this pending transaction and submits a `SystemInstruction::Transfer` sending `1` lamport into the nonce account, landing before the victim's transaction.
3. Nonce account balance becomes `B + 1`. The victim's transaction now hits `lamports (B) != from.get_lamports() (B+1)`, taking the partial-withdraw branch at [3](#0-2) , which requires `B + rent_min <= B + 1`; since `rent_min > 1`, this fails with `InstructionError::InsufficientFunds`, reverting the victim's close-nonce transaction while the attacker's cost was 1 lamport plus a transaction fee.

Uncertainty: I was not able to fully trace the System Program `Transfer` instruction handler itself (only partially read `system_processor.rs`) within this session to give a line-level citation confirming it imposes no ownership restriction on the destination account for lamport-only transfers; this is standard, well-documented Solana System Program behavior, but a full citation of the `Transfer` match arm in `system_processor.rs` should be obtained to fully corroborate this PoC before treating it as final.

### Citations

**File:** programs/system/src/system_instruction.rs (L125-151)
```rust
        State::Initialized(data) => {
            if lamports == from.get_lamports() {
                let durable_nonce =
                    DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
                if data.durable_nonce == durable_nonce {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: nonce can only advance once per slot"
                    );
                    return Err(SystemError::NonceBlockhashNotExpired.into());
                }
                check_signer(&data.authority)?;
                from.set_state(&Versions::new(State::Uninitialized))?;
            } else {
                let min_balance = rent.minimum_balance(from.get_data().len());
                let amount = checked_add(lamports, min_balance)?;
                if amount > from.get_lamports() {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: insufficient lamports {}, need {}",
                        from.get_lamports(),
                        amount,
                    );
                    return Err(InstructionError::InsufficientFunds);
                }
                check_signer(&data.authority)?;
            }
```
