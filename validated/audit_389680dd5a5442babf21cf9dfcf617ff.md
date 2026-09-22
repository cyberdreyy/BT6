### Title
Nonce account `WithdrawNonceAccount` full-withdrawal path can be griefed via exact lamports equality check - (File: `programs/system/src/system_instruction.rs`)

### Summary
`withdraw_nonce_account` in `programs/system/src/system_instruction.rs` decides whether a withdrawal is a "close/full withdraw" (which deinitializes the nonce account and allows the account to be fully drained without a rent-exempt minimum reserved) versus a "partial withdraw" (which must leave `rent.minimum_balance` behind) by comparing the requested `lamports` argument to the account's *current* lamport balance using strict equality: `if lamports == from.get_lamports()`.

### Finding Description
The relevant branch is: [1](#0-0) 

An authorized nonce-account withdrawer who wants to fully close/drain a nonce account typically builds the `WithdrawNonceAccount` instruction with `lamports` equal to the account's balance observed at the time they crafted the transaction (e.g., via `getBalance` or `get_account`). Because the account balance is not read atomically with transaction submission, **anyone** (not just the authority) can permissionlessly increase the nonce account's lamport balance before this transaction lands — e.g., via a plain SOL `Transfer` instruction targeting the nonce account's pubkey, which is public information contained in the durable-nonce account and observable on-chain. This is directly analogous to the reported `SingleTokenJoin` bug, where sending extra tokens to a contract breaks an exact-equality check meant to represent "full expected amount."

Once the actual on-chain balance no longer exactly equals the `lamports` value baked into the previously-signed transaction, the equality check `lamports == from.get_lamports()` fails, and execution falls into the `else` branch, which now requires `lamports + rent.minimum_balance(...) <= from.get_lamports()`. If the withdrawer's `lamports` value was chosen to fully drain the account (i.e., it was equal to the entire prior balance), it will now exceed `from.get_lamports() - min_balance`, causing the transaction to fail with `InstructionError::InsufficientFunds` — a griefing/DoS vector on a specific, otherwise-valid transaction from a fee-paying signer, at the cost of the attacker sending the target extra lamports.

### Impact Explanation
This lets any unprivileged transaction sender grief another user's `WithdrawNonceAccount { lamports = full_balance }` transaction by front-running it with a trivial `Transfer` of lamports into the target nonce account, causing the victim's close/drain transaction to fail deterministically. This matches the class of the external report (griefing via balance-equality mismatch) but the concrete consensus-relevant effect here is limited to instruction failure (transaction fails, fee still charged to the victim) — it does not itself cause fund loss, minting, or a consensus divergence, since all validators would compute the same (deterministic) failure given the same on-chain state. There is no direct fund movement or double-spend; the worst outcome is repeated griefing/DoS against a specific victim transaction/withdrawal attempt, forcing them to re-derive `lamports` and retry (potentially indefinitely if the attacker keeps re-donating lamports).

### Likelihood Explanation
Likelihood is moderate: exploiting it requires the attacker to know the target nonce account pubkey and to race a small SOL transfer ahead of the victim's withdraw transaction landing, which is straightforward for any observer of pending nonce-account withdraw transactions (nonce transactions are not encrypted and their target accounts are visible before/at submission). However, actual damage is only a stalled/failed withdrawal (fee cost + retry), not fund theft, which limits severity relative to the original report's finding (which caused unconditional failure of essentially any join call touching the same token).

### Recommendation
Change the "full withdrawal / close" detection to be based on intent rather than an exact-equality snapshot comparison — e.g., require the withdraw amount to be `>=` the account balance (in which case treat it as full close and transfer only the actual remaining balance), or have callers explicitly request account closure via a dedicated flag/instruction, mirroring the C4 report's recommended fix of relaxing `==` to `>=`.

### Proof of Concept
1. Withdraw authority `W` observes nonce account `N` has balance `B` and builds/signs a `WithdrawNonceAccount(N, to=W, lamports=B)` transaction intending to fully close `N`.
2. Attacker `A` (unprivileged) submits a `Transfer` transaction sending `1` lamport to `N` before `W`'s transaction is processed, increasing `N`'s balance to `B+1`.
3. When `W`'s transaction executes, `lamports (B) == from.get_lamports() (B+1)` is `false`, so execution falls to the `else` branch at [2](#0-1) , requiring `B + rent.minimum_balance(data_len) <= B+1`, which is false unless `min_balance <= 1`. The transaction fails with `InstructionError::InsufficientFunds`, griefing `W`'s intended full withdrawal.

### Citations

**File:** programs/system/src/system_instruction.rs (L125-152)
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
        }
```
