### Title
Nonce account withdrawal uses an unprivileged, attacker-manipulable equality check to decide the closure code path - (File: `programs/system/src/system_instruction.rs`)

### Summary
`withdraw_nonce_account` branches on whether the requested withdrawal amount exactly equals the account's current lamport balance (`lamports == from.get_lamports()`) to decide whether the withdrawal is a full account closure (bypassing the rent-exempt minimum requirement) or a partial withdrawal (which must leave at least `rent.minimum_balance(...)` behind). Because any unprivileged actor can transfer arbitrary lamports to any account, including a nonce account, at any time, this equality condition can be trivially broken by a third party, forcing an intended "close the nonce account" withdrawal onto the partial-withdrawal path and causing it to fail with `InstructionError::InsufficientFunds`. [1](#0-0) 

### Finding Description
The `withdraw_nonce_account` instruction handler determines control flow using a strict equality comparison between the requested `lamports` amount and the account's current balance: [2](#0-1) 

- If `lamports == from.get_lamports()`, the code treats the withdrawal as a full closure: it skips the rent-exempt minimum check and transitions the account to `State::Uninitialized`.
- If the amounts differ, the code takes the "partial withdrawal" branch, which requires `lamports + rent.minimum_balance(data_len) <= from.get_lamports()`, i.e., the account must remain rent-exempt after the withdrawal.

A withdrawal transaction is constructed off-chain with a specific `lamports` value equal to the nonce account's balance observed at construction time. Because lamport transfers to any account (including nonce accounts, which are owned by the System Program) require no signature or permission from the recipient, any unprivileged party can send a dust transfer (e.g., 1 lamport) to the target nonce account before the withdrawal transaction lands. This changes `from.get_lamports()` so it no longer equals the hardcoded `lamports` value in the pending withdrawal instruction, forcing execution into the "partial withdrawal" branch. If the intent was to fully drain and close the account, the partial-withdrawal branch will now require leaving the rent-exempt minimum behind, and if the withdrawal amount does not satisfy `amount <= from.get_lamports() - min_balance`, the instruction fails with `InstructionError::InsufficientFunds`, and the withdraw transaction is aborted.

This mirrors the reported bug class in the external report: an equality check (`Comparison.EQUAL`) is used on a balance value that is trivially manipulable by any external actor, allowing that actor to force the transaction into an unintended state or to deny expected functionality.

### Impact Explanation
The impact is a griefing/denial-of-service on a legitimate nonce-account closure or exact-balance withdrawal: a third party can repeatedly (and cheaply, at the cost of 1 lamport plus a transaction fee) prevent a targeted party from cleanly closing a durable nonce account via the "full withdrawal" fast path. The failure mode is bounded to `InstructionError::InsufficientFunds` for that specific transaction — it does not directly move or steal funds, nor does it cause any consensus-affecting state corruption. This is a genuine but low-severity availability/griefing issue rather than a fund-theft or memory-safety issue.

### Likelihood Explanation
Likelihood is high in the sense that the manipulation is trivial (anyone can send 1 lamport to any known nonce account address) and requires no special privilege, matching the report's characterization of the underlying weakness ("any actor" can manipulate the balance). However, the practical utility of griefing is limited: the affected party can simply resubmit a corrected/current-balance withdrawal instruction, and the attacker must re-donate to continue blocking each attempt, making sustained denial costly relative to its impact.

### Recommendation
Avoid gating rent-exemption bypass strictly on `lamports == from.get_lamports()`. Instead, determine "full closure" intent explicitly (e.g., a dedicated close/withdraw-all instruction or a sentinel value) rather than inferring it from an exact balance equality that any external party can invalidate by donating lamports. Alternatively, treat any withdrawal that would leave the account below the rent-exempt minimum but above zero as a failure only if it does not fully zero the account, and treat "drains account to zero" (rather than "equals the pre-computed instruction amount") as the closure condition, so that unsolicited incoming transfers cannot invalidate the intended withdrawal semantics.

### Proof of Concept
1. Alice creates and funds a durable nonce account `N` with balance `min_balance` (rent-exempt minimum for `nonce::state::State`).
2. Alice observes `N`'s balance as `min_balance` and constructs/signs a `WithdrawNonceAccount` instruction with `lamports = min_balance`, intending to fully close `N` (this hits the `lamports == from.get_lamports()` branch, bypassing the rent check, per [3](#0-2) ).
3. Before Alice's transaction is processed, Mallory (an unrelated, unprivileged actor) submits a system transfer of 1 lamport to `N`, making its balance `min_balance + 1`.
4. Alice's withdrawal transaction now executes with `lamports (min_balance) != from.get_lamports() (min_balance + 1)`, forcing the "else" branch at [4](#0-3) , which requires `lamports + min_balance <= from.get_lamports()`, i.e., `min_balance + min_balance <= min_balance + 1`, which is false for any non-trivial rent-exempt minimum.
5. The instruction returns `InstructionError::InsufficientFunds`, and Alice's intended full withdrawal/closure fails, even though she is the sole authority and rightful controller of the funds. This is directly analogous to the reported `Comparison.EQUAL` balance-check weakness: an externally, freely manipulable balance value used in a strict equality gate for a security-relevant branch decision.

### Citations

**File:** programs/system/src/system_instruction.rs (L111-152)
```rust
    let state: Versions = from.get_state()?;
    match state.state() {
        State::Uninitialized => {
            if lamports > from.get_lamports() {
                ic_msg!(
                    invoke_context,
                    "Withdraw nonce account: insufficient lamports {}, need {}",
                    from.get_lamports(),
                    lamports,
                );
                return Err(InstructionError::InsufficientFunds);
            }
            check_signer(from.get_key())?;
        }
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
        }
```
