### Title
Denial-of-Service via Address Pre-Funding Griefing on `SystemInstruction::CreateAccount`/`CreateAccountWithSeed` — (File: `programs/system/src/system_processor.rs`)

### Summary
The Agave `system_processor` implements account creation such that any address that already holds `> 0` lamports permanently fails `CreateAccount` and `CreateAccountWithSeed` with `AccountAlreadyInUse`. Since many account addresses used by clients, CLI tooling, and CPI'd programs are deterministically derivable (via `Pubkey::create_with_seed` or PDA-style derivation) before the owning transaction is submitted, an attacker can pre-fund the target address with a trivial lamport transfer and permanently block the legitimate creation instruction — the exact "predictable target address griefing" bug class described in the external report (predictable CREATE2 facilitator address pre-funded to block token receipt).

### Finding Description
`create_account` in `system_processor.rs` unconditionally rejects account creation if the destination already has lamports: [1](#0-0) 

This function backs both `SystemInstruction::CreateAccount` and `SystemInstruction::CreateAccountWithSeed`: [2](#0-1) 

Because `CreateAccountWithSeed`'s target address is fully computable in advance from `(base, seed, owner)` via `Pubkey::create_with_seed`, any observer can precompute the address before the legitimate creator submits their transaction (analogous to precomputing a CREATE2 address from `(sender, nonce)` in the Solidity report). The attacker only needs to send 1 lamport to that address ahead of time via an ordinary `SystemInstruction::Transfer` to a not-yet-existing account (system-owned accounts with 0 lamports can receive transfers). Once funded, any subsequent `CreateAccount`/`CreateAccountWithSeed` targeting that exact address will hit the `to.get_lamports() > 0` check and revert with `SystemError::AccountAlreadyInUse`, permanently.

Agave's own developers recognized and fixed this exact class of bug by adding `create_account_allow_prefund` / `SystemInstruction::CreateAccountAllowPrefund`, explicitly documented as: "Create a new account without checking for 0 lamports... Intended for use where account has already had rent paid in whole or in part before creation": [3](#0-2) 

This mitigation is gated behind a feature flag and is opt-in per call site: [4](#0-3) 

Numerous first-party call sites still use the vulnerable legacy path with seed-derived (predictable) addresses instead of the new prefund-tolerant instruction, e.g. CLI nonce-account creation with a user-supplied seed: [5](#0-4) [6](#0-5) 

and CLI stake-account creation with seed: [7](#0-6) 

### Impact Explanation
This is a griefing/denial-of-service primitive reachable by any unprivileged user via a single low-cost `Transfer` instruction targeting a precomputed address. It permanently and irrecoverably blocks the intended account (nonce account, stake account, escrow/vault, or any protocol-derived seed account) from ever being created at that address via the standard `CreateAccount`/`CreateAccountWithSeed` path, forcing the victim to abandon that derived address (loss of any pre-registered references to it) — mirroring the "sinkManager stuck in current state" impact in the Solidity report. Because `CreateAccountAllowPrefund` is feature-gated and not the default path for all seed-derived account creation flows in the codebase, exposure persists wherever legacy `CreateAccount`/`CreateAccountWithSeed` is still used against a derivable address.

### Likelihood Explanation
High likelihood: the attack requires only knowledge of the deterministic derivation inputs (base pubkey/authority, seed string, owner program id) — all of which are either publicly known conventions (e.g., well-known seed strings) or observable from the victim's off-chain preparation/CLI usage before the on-chain creation transaction lands — plus one cheap `Transfer` instruction. No privileged state or special program interaction is needed.

### Recommendation
- Migrate all first-party seed-derived account creation flows (nonce, stake, and any protocol-style account creation via `CreateAccountWithSeed`) to `SystemInstruction::CreateAccountAllowPrefund`, or make it the default behavior for `CreateAccountWithSeed` rather than opt-in.
- Consider unifying `create_account_allow_prefund` semantics into `create_account` for any address that is provably derived from a seed/PDA (thus not attacker-race-condition-free by design anyway), while keeping the strict zero-lamports check only for pure random keypair-based accounts where prefunding could indicate account reuse/malicious activity.

### Proof of Concept
1. Off-chain, attacker observes/derives `to = Pubkey::create_with_seed(&base, &seed, &owner)` for a victim's planned nonce/stake account (as done in `cli/src/nonce.rs:463-467`) before the victim's `CreateAccountWithSeed` transaction lands.
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` to `to` (a valid target since `to` is currently an empty, system-owned, 0-data account).
3. Victim submits `SystemInstruction::CreateAccountWithSeed` targeting `to`; `create_account` (`programs/system/src/system_processor.rs:160-174`) observes `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`, permanently preventing creation at that address.

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

**File:** programs/system/src/system_processor.rs (L330-378)
```rust
        SystemInstruction::CreateAccount {
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                None,
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }

        SystemInstruction::CreateAccountWithSeed {
            base,
            seed,
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                Some((&base, &seed, &owner)),
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

**File:** programs/system/src/system_processor.rs (L530-547)
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
```

**File:** cli/src/nonce.rs (L463-467)
```rust
    let nonce_account_address = if let Some(ref seed) = seed {
        Pubkey::create_with_seed(&nonce_account_pubkey, seed, &system_program::id())?
    } else {
        nonce_account_pubkey
    };
```

**File:** cli/tests/nonce.rs (L277-293)
```rust
    let seed = authority_pubkey.to_string()[0..32].to_string();
    let nonce_address =
        Pubkey::create_with_seed(&creator_pubkey, &seed, &system_program::id()).unwrap();
    check_balance!(0, &rpc_client, &nonce_address);

    let mut creator_config = CliConfig::recent_for_tests();
    creator_config.json_rpc_url = test_validator.rpc_url();
    creator_config.signers = vec![&online_nonce_creator_signer];
    creator_config.command = CliCommand::CreateNonceAccount {
        nonce_account: 0,
        seed: Some(seed),
        nonce_authority: Some(authority_pubkey),
        memo: None,
        amount: SpendAmount::Some(241 * LAMPORTS_PER_SOL),
        compute_unit_price: None,
    };
    process_command(&creator_config).await.unwrap();
```

**File:** cli/tests/stake.rs (L2231-2251)
```rust
    // Create another stake account. This time with seed
    let seed = "seedy";
    config_offline.signers = vec![&offline_signer, &stake_keypair];
    config_offline.command = CliCommand::CreateStakeAccount {
        stake_account: 1,
        seed: Some(seed.to_string()),
        staker: None,
        withdrawer: None,
        withdrawer_signer: None,
        lockup: Lockup::default(),
        amount: SpendAmount::Some(50_000_000_000),
        sign_only: true,
        dump_transaction_message: false,
        blockhash_query: BlockhashQuery::Static(nonce_hash),
        nonce_account: Some(nonce_pubkey),
        nonce_authority: 0,
        memo: None,
        fee_payer: 0,
        from: 0,
        compute_unit_price,
    };
```
