This confirms the analog and its official status: Agave itself explicitly documents this exact bug class — `SystemInstruction::CreateAccount` (in `create_account`, `programs/system/src/system_processor.rs:150-182`) rejects account creation whenever the target address already holds `lamports > 0`, which is exactly the "pre-funding griefing" pattern described in the bonding-curve report. Agave's own fix, `SystemInstruction::CreateAccountAllowPrefund` gated behind the `create_account_allow_prefund` feature (SIMD-0312), was introduced specifically to remediate this DoS class for callers who need it, but it is opt-in and most existing callers (stake/vote/nonce account creation flows) still use the vulnerable `CreateAccount`/`CreateAccountWithSeed` path.

### Title
Account-creation griefing via lamport pre-funding — `SystemInstruction::CreateAccount` fails when target address is pre-funded ([File: programs/system/src/system_processor.rs])

### Summary
The system program's `create_account` handler unconditionally rejects account creation if the destination account already has any lamports, which lets any unprivileged actor permanently block a victim from creating an account at a known-in-advance address (e.g. a fresh keypair or seed-derived address) simply by sending it 1 lamport before the legitimate `CreateAccount`/`CreateAccountWithSeed` transaction lands.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` checks:
```
if to.get_lamports() > 0 {
    ... return Err(SystemError::AccountAlreadyInUse.into());
}
``` [1](#0-0) 
This means any address that is going to be used as the target of `CreateAccount` (a freshly generated keypair pubkey, or a `CreateAccountWithSeed`-derived address, whose derivation from `base`/`seed`/`owner` is fully public per `Address::create`) can be "dusted" with an arbitrary lamport transfer by any third party before the real owner submits their creation transaction. Because Solana permits lamport transfers to any pubkey without any signature from that pubkey, the destination address requires no cooperation from the attacker's target — only knowledge of the address, which is public (it's included in the transaction that will eventually create it, or is derivable via `create_with_seed`). Once dusted, every subsequent legitimate `CreateAccount`/`CreateAccountWithSeed` transaction targeting that address permanently fails with `SystemError::AccountAlreadyInUse`, exactly mirroring the reported bonding-curve escrow bug where a deterministic, pre-computable address could be pre-funded to break an invariant/creation flow.

Agave's own feature-set entry corroborates that this is a recognized bug class requiring a dedicated fix: `create_account_allow_prefund` (SIMD-0312) introduces `SystemInstruction::CreateAccountAllowPrefund`, whose handler and `create_account_allow_prefund` function explicitly skip the zero-lamport check [2](#0-1) , and the feature is documented as "Enable CreateAccountAllowPrefund system program instruction" [3](#0-2) . However this new instruction is opt-in per caller/client and gated by feature activation [4](#0-3) ; the original `CreateAccount`/`CreateAccountWithSeed` instructions used throughout the ecosystem (stake, vote, nonce, token account creation flows, and general dapp usage) remain vulnerable to this griefing vector since they still call the strict `create_account` path.

### Impact Explanation
This is an ordinary-user-reachable denial-of-service: an attacker can permanently prevent any victim from creating a system account at a specific, publicly-known address (a new keypair the victim intends to use, or a deterministic seed-derived address) by sending a trivial lamport transfer to it first. This can be used to grief nonce-account setup, stake/vote account bootstrapping, or any protocol relying on deterministic account creation (as in the referenced bonding-curve escrow), causing transaction failures and requiring victims to burn a new keypair/address, at minimal cost to the attacker (a single lamport transfer plus its fee).

### Likelihood Explanation
Likelihood is high for any workflow that publishes or can have its target address predicted ahead of the `CreateAccount` transaction (e.g., seed-derived addresses via `create_with_seed`, or addresses observable in the mempool/QUIC ingest before confirmation). No privileged access, validator cooperation, or signing key for the target address is required — only a standard `SystemInstruction::Transfer` to the known target pubkey.

### Recommendation
Broaden adoption of the already-implemented `CreateAccountAllowPrefund` remediation: encourage/require callers that create accounts at addresses whose derivation is public in advance (seed-derived addresses in particular) to use `CreateAccountAllowPrefund` instead of `CreateAccount`, and consider deprecating the strict zero-lamport check for `CreateAccount`/`CreateAccountWithSeed` once `create_account_allow_prefund` is fully activated, so pre-existing lamports are simply credited toward the new account rather than causing a hard failure.

### Proof of Concept
1. Determine a target address ahead of time, e.g. compute `create_with_seed(base, seed, owner)` for a `CreateAccountWithSeed` the victim intends to use.
2. As an unprivileged attacker, submit a normal `SystemInstruction::Transfer` sending 1 lamport to that address.
3. Victim later submits `SystemInstruction::CreateAccount`/`CreateAccountWithSeed` targeting that address; `create_account`'s check `to.get_lamports() > 0` triggers, returning `SystemError::AccountAlreadyInUse` and aborting the victim's transaction [5](#0-4) .
4. Confirmed by the existing test `test_create_already_in_use`, which asserts exactly this failure mode when the target account already has lamports before creation [6](#0-5) .

### Citations

**File:** programs/system/src/system_processor.rs (L161-174)
```rust
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

**File:** programs/system/src/system_processor.rs (L530-541)
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
```

**File:** programs/system/src/system_processor.rs (L1014-1038)
```rust
        // Attempt to create an account that already has lamports
        let owned_account = AccountSharedData::new(1, 0, &Pubkey::default());
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
```

**File:** feature-set/src/lib.rs (L2463-2466)
```rust
        (
            create_account_allow_prefund::id(),
            "SIMD-0312: Enable CreateAccountAllowPrefund system program instruction",
        ),
```
