### Title
Griefable exact-balance equality check in `withdraw_nonce_account` allows front-run DoS of nonce account closure - (File: `programs/system/src/system_instruction.rs`)

### Summary
The `WithdrawNonceAccount` system instruction handler uses an exact `==` comparison between the requested withdrawal amount and the nonce account's current lamport balance to decide whether the withdrawal fully drains (and deinitializes) the nonce account, bypassing the rent-exempt minimum-balance requirement. Because any unprivileged account can send lamports to an arbitrary nonce account via a plain `SystemInstruction::Transfer` (no signature/permission from the recipient is required), an attacker can donate a small amount of lamports to a target nonce account to break this equality check and force a legitimate "withdraw everything and close" transaction to revert with `InsufficientFunds`. This is the same bug class as the referenced Behodler `UniswapHelper` finding: an unprivileged actor sending funds directly to a contract/account to break an `==` assumption used for a critical accounting decision.

### Finding Description
In `withdraw_nonce_account`, when the nonce state is `Initialized`, the code branches on whether the caller is requesting to withdraw the account's *entire* current balance: [1](#0-0) 

```rust
State::Initialized(data) => {
    if lamports == from.get_lamports() {
        // ... full withdrawal path: deinitializes nonce, skips min_balance check
        from.set_state(&Versions::new(State::Uninitialized))?;
    } else {
        // partial withdrawal path: requires lamports + min_balance <= balance
        let min_balance = rent.minimum_balance(from.get_data().len());
        let amount = checked_add(lamports, min_balance)?;
        if amount > from.get_lamports() {
            return Err(InstructionError::InsufficientFunds);
        }
        check_signer(&data.authority)?;
    }
}
```

The `lamports` parameter is chosen client-side (e.g. by the CLI, which reads the current on-chain balance via RPC and constructs the withdraw instruction with `lamports == current_balance` to fully close the account, as seen in `cli/src/nonce.rs`/vote withdraw flows). Because *any* account can transfer lamports to the nonce account address between the time the balance is queried and the time the `WithdrawNonceAccount` instruction executes (system `Transfer` requires no permission from the recipient), an attacker can insert a `Transfer` of even 1 lamport to the target nonce account, landing before the withdraw transaction in the same or an earlier slot (trivially achievable by paying a higher priority fee to control ordering).

Once the donation lands, `from.get_lamports()` no longer equals the `lamports` value baked into the pending withdraw transaction, so the code falls into the `else` branch, which now requires `lamports + min_balance <= from.get_lamports()`. If the donated amount `D` is smaller than `min_balance`, this check fails (`lamports + min_balance > lamports + D`), and the entire withdrawal transaction returns `InstructionError::InsufficientFunds`, reverting the intended full-withdraw/close operation.

### Impact Explanation
This allows any unprivileged party to griefing-DoS a specific nonce account's "withdraw all and close" operation by racing a tiny, permissionless lamport transfer ahead of the withdrawal transaction. The nonce authority's legitimate, correctly-signed instruction is forced to fail even though they hold full authority over the funds and intended state transition. An attacker can repeat this indefinitely (each time donating a fresh tiny amount) to persistently block closure/full-withdrawal of a targeted nonce account, which is a state-mutation-prevention griefing vector analogous to the original report (unprivileged token/lamport donation breaking an `==`-based accounting assumption). Impact is bounded to availability/griefing of the withdrawal-and-close path (a revert/DoS), not fund loss, matching the "recoverable but disruptive" characterization noted by the original finding's disclosure/acknowledgment.

### Likelihood Explanation
Likelihood is high for a determined attacker targeting a specific nonce account they wish to grief: sending lamports via `SystemInstruction::Transfer` requires no special privilege, and controlling transaction ordering relative to a known pending withdraw transaction is achievable with normal priority-fee bidding/QUIC ingest timing. The attack is cheap (as little as 1 lamport plus a transaction fee) and repeatable.

### Recommendation
Replace the exact `lamports == from.get_lamports()` equality test with a `>=` check (i.e., treat any request for `lamports` that is at least the *current* balance, or better, decouple "full withdraw" semantics from a client-supplied exact amount by having the caller explicitly request "withdraw all" and have the program compute the amount from the live balance at execution time, only closing/deinitializing when the resulting balance would be zero). This removes the ability for third-party donations to flip which branch is taken and eliminates the front-running griefing vector.

### Proof of Concept
1. Nonce authority queries the current nonce account balance `X` via RPC and constructs a `WithdrawNonceAccount { lamports: X }` transaction intended to fully withdraw and close the account (as done in the CLI nonce-withdraw flow, `cli/src/nonce.rs`).
2. Before that transaction lands, an attacker submits an ordinary `SystemInstruction::Transfer` sending `D` lamports (with `0 < D < rent.minimum_balance(nonce_account_data_len)`) to the same nonce account address, using a competitive priority fee to ensure it is processed first.
3. When the withdraw transaction executes, `from.get_lamports()` is now `X + D`, so `lamports (X) == from.get_lamports() (X + D)` is false; the code takes the `else` branch in `withdraw_nonce_account` (`programs/system/src/system_instruction.rs:138-151`), computing `amount = X + min_balance`, which exceeds the actual balance `X + D` (since `D < min_balance`), causing the instruction—and the whole transaction—to fail with `InstructionError::InsufficientFunds`.
4. The nonce authority's intended full-withdraw-and-close operation reverts, and the attacker can repeat step 2 against any future retry to keep griefing the same account.

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
