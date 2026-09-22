### Title
Denial-of-service via lamport pre-funding front-run on `SystemInstruction::CreateAccount` / `CreateAccountWithSeed` - (File: `programs/system/src/system_processor.rs`)

### Summary
The `create_account` handler in Agave's system program rejects account creation whenever the destination account already holds any lamports, returning `SystemError::AccountAlreadyInUse`. Because destination pubkeys used with `CreateAccount`/`CreateAccountWithSeed` (and by extension well-known derived addresses such as Associated Token Accounts) are fully predictable from public information before the creating transaction lands, any unprivileged actor can front-run a pending "create account" transaction by transferring a trivial amount of lamports to that address first, deterministically causing the victim's create-account transaction to fail. This mirrors the reported `boreWell` DoS vector, where an attacker races to occupy a deterministic target address computed from public inputs (`salt`), causing the legitimate creator's transaction to revert.

### Finding Description
`create_account` in `system_processor.rs` unconditionally bails out if the target account is already funded: [1](#0-0) 

The destination address for `CreateAccount` is typically a fresh keypair known to the submitter ahead of time (e.g. published in a pending, unconfirmed transaction visible in the mempool/gossip of unconfirmed txs, or a deterministically derived address such as an Associated Token Account computed from `wallet + mint`). Because Solana's account-creation flow separates "pubkey is known/derivable" from "pubkey's lamports are protected," any account holding a non-zero lamport balance — regardless of who put the lamports there — makes the account ineligible for `CreateAccount`.

An attacker observing a pending create-account transaction (or simply pre-computing a commonly derived address, like an ATA, before the owner ever gets to create it) can submit a trivial `SystemInstruction::Transfer` of 1 lamport to that address. This transaction has no special permission requirements — it's an ordinary system transfer reachable by any unprivileged transaction sender. Once landed, the victim's subsequent `CreateAccount`/`CreateAccountWithSeed` instruction will always fail with `AccountAlreadyInUse`, exactly the same "attacker races to occupy the deterministic target address, and the victim's create-account transaction reverts" DoS pattern described in the `boreWell` report.

This exact failure mode was recognized by the Agave team: a new instruction, `SystemInstruction::CreateAccountAllowPrefund`, and matching feature gate `create_account_allow_prefund`, were added specifically to allow account creation on top of pre-funded/pre-existing lamport balances: [2](#0-1) [3](#0-2) 

Until/unless callers migrate to `CreateAccountAllowPrefund`, all existing callers of the legacy `CreateAccount`/`CreateAccountWithSeed` path (which is the vast majority of on-chain programs, wallets, and CLI tooling, including SPL Associated Token Account creation) remain exposed to this address-squatting DoS.

### Impact Explanation
This is a availability/DoS issue reachable from a single, ordinary, unprivileged transaction (`SystemInstruction::Transfer`). It does not itself move or steal funds — Solana's atomic transaction execution means a failed `CreateAccount` instruction aborts the whole enclosing transaction, so it does not reproduce the "theft of user deposit" half of the original `boreWell` report. It does, however, reliably block legitimate users (or programs, e.g. wallets creating Associated Token Accounts, stake accounts, or nonce accounts for other parties) from creating accounts at predictable addresses, and can be repeated indefinitely at negligible cost (1 lamport + a transfer fee) to keep denying service, matching the "DoS for boreWell" attack vector precisely.

### Likelihood Explanation
High reachability: any account holder can send a 1-lamport transfer to any known public key. The only requirement is that the attacker learn/derive the target address before the victim's create-account transaction confirms — trivial for commonly-derived addresses like Associated Token Accounts, or for addresses observable in the public mempool of pending transactions.

### Recommendation
Encourage/point integrators toward `SystemInstruction::CreateAccountAllowPrefund` (already implemented behind the `create_account_allow_prefund` feature gate) for any workflow where the destination address is predictable ahead of transaction landing, since it explicitly tolerates a pre-funded target account: [4](#0-3) 

For legacy `CreateAccount`/`CreateAccountWithSeed` callers (e.g. SPL Associated Token Account program) that cannot be changed, they should either move to the idempotent/prefund-tolerant creation flow or accept the pre-funding as legitimate rent contribution rather than treating any non-zero balance as "in use."

### Proof of Concept
1. Alice (or a program acting on Alice's behalf) prepares a transaction that creates a fresh account `X` via `SystemInstruction::CreateAccount` (or derives `X` deterministically, e.g. as an Associated Token Account for her wallet/mint).
2. Before Alice's transaction confirms, Bob (unprivileged attacker) submits `SystemInstruction::Transfer` of 1 lamport to `X`.
3. Bob's transfer lands first (fee bump or simple timing).
4. Alice's `CreateAccount` instruction now hits: [5](#0-4) 
and fails with `SystemError::AccountAlreadyInUse`, aborting Alice's transaction.
5. Bob repeats step 2 for every subsequent retry by Alice, achieving persistent denial of service on account creation at address `X` at negligible cost per attempt.

### Citations

**File:** programs/system/src/system_processor.rs (L160-174)
```rust
) -> Result<(), InstructionError> {
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(
                invoke_context,
                "Create Account: account {:?} already in use",
                to_address
            );
            return Err(SystemError::AccountAlreadyInUse.into());
        }

        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
```

**File:** programs/system/src/system_processor.rs (L184-214)
```rust
/// Create a new account without checking for 0 lamports. All other checks remain.
/// Intended for use where account has already had rent paid in whole or in part
/// before creation.
#[allow(clippy::too_many_arguments)]
fn create_account_allow_prefund(
    to_account_index: IndexOfAccount,
    to_address: &Address,
    from_and_lamports: Option<(IndexOfAccount, u64)>,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    if let Some((from_account_index, lamports)) = from_and_lamports
        && lamports > 0
    {
        transfer(
            from_account_index,
            to_account_index,
            lamports,
            invoke_context,
            instruction_context,
        )?;
    }
    Ok(())
}
```

**File:** programs/system/src/system_processor.rs (L530-563)
```rust
        SystemInstruction::CreateAccountAllowPrefund {
            lamports,
            space,
            owner,
        } => {
            if !invoke_context
                .get_feature_set()
                .create_account_allow_prefund
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            let from_and_lamports = if lamports > 0 {
                instruction_context.check_number_of_instruction_accounts(2)?;
                Some((1, lamports))
            } else {
                instruction_context.check_number_of_instruction_accounts(1)?;
                None
            };
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(0)?,
                None,
                invoke_context,
            )?;
            create_account_allow_prefund(
                0,
                &to_address,
                from_and_lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```
