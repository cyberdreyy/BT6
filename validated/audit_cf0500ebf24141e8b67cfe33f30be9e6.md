I have sufficient evidence to write the final analog report.

### Title
Griefing via unsolicited lamport pre-funding blocks `SystemInstruction::CreateAccount`, causing legitimate account-creation transactions to permanently fail - (File: programs/system/src/system_processor.rs)

### Summary
The System Program's `create_account` handler rejects account creation whenever the target address already holds any lamports, exactly mirroring the Sandclock `Vault.setStrategy` pattern where a zero-balance precondition is trivially griefable by anyone who can transfer value to the guarded address.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` enforces:
```
if to.get_lamports() > 0 {
    return Err(SystemError::AccountAlreadyInUse.into());
}
``` [1](#0-0) 

Because System Program transfers to an arbitrary pubkey require no signature or cooperation from the target account, any unprivileged user can send 1 lamport (or any nonzero amount) to a target address before its intended owner submits a `CreateAccount` instruction for it. Once lamports are present, every subsequent `CreateAccount` attempt against that address fails with `SystemError::AccountAlreadyInUse`, regardless of who initiated it or how many retries are made — the griefer can simply repeat the transfer after each failed attempt, an ongoing DoS identical in structure to the aUST-griefing loop described in the Sandclock report.

This is a long-standing, well-understood limitation of the System Program's `CreateAccount` instruction, and the codebase itself confirms the bug class: a new instruction, `SystemInstruction::CreateAccountAllowPrefund`, was introduced specifically to bypass the zero-lamport precondition ("Create a new account without checking for 0 lamports... Intended for use where account has already had rent paid in whole or in part before creation"), gated behind the `create_account_allow_prefund` feature (SIMD-0312). [2](#0-1) [3](#0-2) [4](#0-3) 

Critically, `CreateAccountAllowPrefund` is opt-in: any caller (including nearly all existing wallets, program deployers, and dApps such as ATA creators) that still issues the legacy `SystemInstruction::CreateAccount` instruction — which remains the default and only widely-supported instruction — is unprotected. The `allocate`/`assign` path used by `AllocateWithSeed`/`AssignWithSeed` checks only `data.is_empty()` and owner, not lamports, so it isn't vulnerable to this specific griefing vector, underscoring that `CreateAccount`'s lamport check is the outlier introducing the risk. [5](#0-4) 

Target addresses are frequently deterministic and publicly derivable ahead of time (e.g., PDAs, `create_with_seed` addresses, or addresses observable in-flight via QUIC/TPU ingest before confirmation), giving an attacker ample opportunity to front-run and repeatedly grief legitimate `CreateAccount` transactions from ordinary users.

### Impact Explanation
This enables a persistent denial-of-service against account creation: a griefer can indefinitely block a specific user, program, or protocol from creating a needed on-chain account (e.g., an associated token account, PDA-backed state account, or vesting/escrow account) by repeatedly pre-funding the target address with a negligible amount of lamports, mirroring the "Impact: Strategy couldn't be changed" outcome of the referenced Sandclock finding.

### Likelihood Explanation
Likelihood is high for any predictable/derivable target address: the attack costs only a small SOL transfer per attempt (refundable to the attacker if the account is never created, since ownership of the lamports remains with the target until reassigned), requires no special privileges, and can be executed by any transaction sender against any address whose creation is anticipated (e.g., watching for `AssociatedTokenAccount`/PDA derivations in mempool or via known deterministic seeds).

### Recommendation
Consider having `create_account` (or a fully-enabled successor) treat pre-existing lamports as fundable balance rather than an "already in use" signal — as `CreateAccountAllowPrefund` already does — and either fully activate that behavior for all `CreateAccount` calls once space/owner are both unset, or provide equivalent zero-cost migration guidance/tooling so callers are not forced to keep using the griefable legacy path. Alternatively, track "in use" status purely via account data/owner (as `allocate` already does) instead of lamport balance.

### Proof of Concept
1. Observe/derive the pubkey a victim intends to pass as the `to` account for a future `SystemInstruction::CreateAccount` instruction (e.g., a PDA, vanity address, or `create_with_seed` address).
2. Submit a `SystemInstruction::Transfer` of 1 lamport from any funded account to that pubkey before the victim's `CreateAccount` transaction lands.
3. When the victim's `CreateAccount` transaction executes, `to.get_lamports() > 0` is true, and the instruction fails with `SystemError::AccountAlreadyInUse` at [6](#0-5) .
4. The attacker repeats step 2 after every retry, indefinitely preventing account creation at that address unless the victim adopts `CreateAccountAllowPrefund` (only usable once/if the `create_account_allow_prefund` feature is activated network-wide).

### Citations

**File:** programs/system/src/system_processor.rs (L91-100)
```rust
    // if it looks like the `to` account is already in use, bail
    //   (note that the id check is also enforced by message_processor)
    if !account.get_data().is_empty() || !system_program::check_id(account.get_owner()) {
        ic_msg!(
            invoke_context,
            "Allocate: account {:?} already in use",
            address
        );
        return Err(SystemError::AccountAlreadyInUse.into());
    }
```

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

**File:** feature-set/src/lib.rs (L1328-1330)
```rust
pub mod create_account_allow_prefund {
    solana_pubkey::declare_id!("6sPDzwyARRExKH52LECxcGoqziH8G7SZofwuxi8Ja331");
}
```

**File:** feature-set/src/lib.rs (L2463-2466)
```rust
        (
            create_account_allow_prefund::id(),
            "SIMD-0312: Enable CreateAccountAllowPrefund system program instruction",
        ),
```
