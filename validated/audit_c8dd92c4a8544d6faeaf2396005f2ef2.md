### Title
Griefing via lamport pre-funding permanently blocks `SystemInstruction::CreateAccount` for known/derivable addresses - (File: programs/system/src/system_processor.rs)

### Summary
`system_processor::create_account()` (the handler for `SystemInstruction::CreateAccount` and `CreateAccountWithSeed`) refuses to initialize an account whenever its lamport balance is already non-zero, returning `SystemError::AccountAlreadyInUse`. Because an ordinary, unprivileged transaction can send lamports to *any* known or derivable public key (vote accounts, stake accounts, nonce accounts, program-derived/seeded addresses) before the legitimate owner submits the actual `CreateAccount` transaction, an attacker can permanently block that account's creation — the same "attacker sets a precondition value that a privileged/expected operation strictly requires to be zero" pattern described in the Locke.sol `incentives[who] == 0` finding.

### Finding Description
`create_account()` enforces: [1](#0-0) 

```
if to.get_lamports() > 0 {
    ... return Err(SystemError::AccountAlreadyInUse.into());
}
```

This check exists to stop double-initialization, but it is keyed purely on *lamport balance*, which is fully attacker-controllable via an ordinary `Transfer` instruction to any public key, including addresses that are:
- Derived via `create_with_seed` (`Address::create` at [2](#0-1) ), which are deterministic and publicly computable from `(base, seed, owner)`.
- Vote/stake/nonce accounts whose pubkeys are published or predictable ahead of the account-creation transaction landing (e.g. multi-instruction flows like the one seen creating a vote account then delegating stake, [3](#0-2) ).

Once lamports > 0, the address can never be initialized through `CreateAccount`/`CreateAccountWithSeed` — this is structurally the same "immutable precondition set by an attacker blocks a legitimate privileged action" pattern as the Locke.sol report, where `incentives[who] == 0` had to hold before `arbitraryCall()` could succeed, and an attacker could force it non-zero via `createIncentive()`.

The codebase's own remediation confirms the class is recognized: `create_account_allow_prefund` / `SystemInstruction::CreateAccountAllowPrefund` was added specifically to bypass the zero-lamport requirement ( [4](#0-3) , gated by `invoke_context.get_feature_set().create_account_allow_prefund` at [5](#0-4) ), i.e. the plain `CreateAccount` path is retained as-is (griefable) while a new instruction variant was introduced as the fix/workaround for this exact prefund-griefing scenario.

Test coverage explicitly documents the blocking behavior: [6](#0-5)  shows that an account pre-funded with even 1 lamport causes `CreateAccount` to fail with `AccountAlreadyInUse`, and unrelated legitimate callers (e.g. tools building `create_account_and_delegate_stake` or vote-account bootstrap flows, [3](#0-2) ) still rely on the vulnerable `CreateAccount`, not the new `CreateAccountAllowPrefund` variant.

### Impact Explanation
Any unprivileged user who can predict or derive the target address (seeded accounts, well-known deployment addresses, or addresses announced ahead of time for vote/stake/nonce/PDA-style setup) can send a trivial number of lamports to that address before the rightful owner's `CreateAccount` transaction lands. This permanently denies initialization of that account through the standard system-program path, forcing the victim to either abandon that address or fall back to `Allocate`+`Assign`+separate `Transfer` (which do not check lamports) — an operational workaround that many higher-level tools/programs do not implement. This is a low-cost, no-privilege denial-of-service against account bootstrap flows (deployment, delegation setup, seeded PDAs) rather than a fund-theft or consensus-safety bug.

### Likelihood Explanation
Moderate. Exploitation requires only a public RPC/QUIC-submitted `Transfer` instruction and knowledge of the target address ahead of the victim's `CreateAccount` transaction landing — realistic for `create_with_seed` derived addresses (fully deterministic from public inputs) and for any multi-step bootstrap flow where the new account's pubkey is generated/published before the creation transaction is broadcast and confirmed (a normal race/mempool-visibility window). It requires no special privilege, matching the "unprivileged transaction" class in scope.

### Recommendation
As with the original report's accepted mitigation, the cleanest fix is not to change the security model (a strict `lamports == 0` check is intentional to avoid clobbering funded accounts) but to make the safer, prefund-tolerant instruction (`CreateAccountAllowPrefund`) the default/expected path for internal callers (vote/stake/nonce bootstrap tooling, `local-cluster`, CLI) rather than an opt-in feature-gated addition, and to document clearly for downstream program/tooling authors that `CreateAccount`/`CreateAccountWithSeed` on a griefed (pre-funded) address will permanently fail, recommending `Allocate`+`Assign` or `CreateAccountAllowPrefund` for any address whose value can be predicted or published before the creation transaction executes.

### Proof of Concept
1. Attacker observes/derives a target pubkey `T` that will later be used as the `to` account in a `SystemInstruction::CreateAccount` (e.g., a seeded address from `Pubkey::create_with_seed(base, seed, owner)`, or a vote/stake account pubkey published ahead of the bootstrap transaction).
2. Attacker submits an ordinary `Transfer` instruction sending 1 lamport to `T`.
3. Victim later submits `SystemInstruction::CreateAccount { lamports, space, owner }` targeting `T`; `create_account()` at [7](#0-6)  sees `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`, exactly reproduced by the existing unit test [6](#0-5) .
4. The account can never be created via `CreateAccount`/`CreateAccountWithSeed` at that address again, permanently denying the intended initialization unless the victim's tooling switches to `Allocate`+`Assign` or the feature-gated `CreateAccountAllowPrefund` path.

### Citations

**File:** programs/system/src/system_processor.rs (L43-72)
```rust
    fn create(
        address: &Pubkey,
        with_seed: Option<(&Pubkey, &str, &Pubkey)>,
        invoke_context: &InvokeContext,
    ) -> Result<Self, InstructionError> {
        let base = if let Some((base, seed, owner)) = with_seed {
            // The conversion from `PubkeyError` to `InstructionError` through
            // num-traits is incorrect, but it's the existing behavior.
            let address_with_seed =
                Pubkey::create_with_seed(base, seed, owner).map_err(|e| e as u64)?;
            // re-derive the address, must match the supplied address
            if *address != address_with_seed {
                ic_msg!(
                    invoke_context,
                    "Create: address {} does not match derived address {}",
                    address,
                    address_with_seed
                );
                return Err(SystemError::AddressWithSeedMismatch.into());
            }
            Some(*base)
        } else {
            None
        };

        Ok(Self {
            address: *address,
            base,
        })
    }
```

**File:** programs/system/src/system_processor.rs (L160-182)
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
    transfer(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
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

**File:** programs/system/src/system_processor.rs (L1014-1041)
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
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);
    }
```

**File:** local-cluster/src/local_cluster.rs (L1062-1082)
```rust
        if rpc_client
            .poll_get_balance_with_commitment(&vote_account_pubkey, CommitmentConfig::processed())
            .unwrap_or(0)
            == 0
        {
            // 1) Create vote account — always use V1 InitializeAccount
            let mut instructions = vote_instruction::create_account_with_config(
                &from_account.pubkey(),
                &vote_account_pubkey,
                &VoteInit {
                    node_pubkey,
                    authorized_voter: vote_account_pubkey,
                    authorized_withdrawer: vote_account_pubkey,
                    commission: 0,
                },
                amount,
                vote_instruction::CreateVoteAccountConfig {
                    space: vote_state::VoteStateV4::size_of() as u64,
                    ..vote_instruction::CreateVoteAccountConfig::default()
                },
            );
```
