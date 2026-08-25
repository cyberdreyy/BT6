### Title
Missing validation of `nonce_authority` in `AuthorizeNonceAccount`/`InitializeNonceAccount` can permanently lock a nonce account's authority - (File: `programs/system/src/system_instruction.rs`)

### Summary
The System Program's nonce-account instruction handlers `initialize_nonce_account` and `authorize_nonce_account` accept an arbitrary `nonce_authority: &Pubkey` argument and write it into the nonce account's `Data` with no sanity check that it is a "real", controllable address (e.g. not `Pubkey::default()`/all-zero, or not some other unintended pubkey). This mirrors the Uniswap `UniswapV3Factory` finding: an unchecked owner/authority argument can permanently brick a privileged role on the affected account.

### Finding Description
`authorize_nonce_account` in [1](#0-0)  takes the caller-supplied `nonce_authority` and, after confirming the *current* authority signed, calls `account.get_state::<Versions>()?.authorize(signers, *nonce_authority)` and writes the resulting state back with no check on the value of `nonce_authority` itself. Likewise `initialize_nonce_account` in [2](#0-1)  stores whatever `nonce_authority` pubkey is supplied into `nonce::state::Data::new(*nonce_authority, ...)` without validating it.

The instruction dispatcher in `system_processor.rs` passes the raw instruction-supplied pubkey straight through: [3](#0-2) . The unit tests explicitly demonstrate that `Pubkey::default()` (the all-zero pubkey, the Solana analog of Solidity's `address(0)`) is accepted as a valid new authority with no error: [4](#0-3) . The Rust nonce CLI path (`process_authorize_nonce_account`) also forwards `new_authority` unchecked into the `authorize_nonce_account` instruction builder: [5](#0-4) .

Once the nonce account's `authorized` field is set to `Pubkey::default()` (or any other address for which the caller does not control a signing key), all future privileged operations on that nonce account — `AuthorizeNonceAccount`, `AdvanceNonceAccount`, `WithdrawNonceAccount` — will fail their `MissingRequiredSignature`/authority checks forever, because no one can produce a valid signature for `Pubkey::default()`. This is functionally identical to the reported Uniswap bug: an unchecked "owner"/authority argument that, once set to an unusable value, permanently locks that role, and the only remedy is abandoning the account (the "costly redeploy" analog is "creating a new nonce account and migrating durable-nonce usage").

### Impact Explanation
Impact is confined to the single nonce account whose authority is mis-set — it is a self-inflicted account-level state lock, not a protocol-wide or cross-account issue, and there is no path to unauthorized fund transfer or privilege escalation over other accounts (the operation always requires the *current* valid authority's signature, so an attacker cannot force this on a victim's nonce account without already having signing rights over it). The realistic impact is loss of availability of durable-nonce functionality for that specific account (funds remain recoverable by System Program's normal account rules since lamports aren't moved), which is a low-severity griefing/self-footgun scenario rather than a scalable exploit.

### Likelihood Explanation
Likelihood is low. This requires either (a) an authority holder deliberately or mistakenly passing `Pubkey::default()`/wrong pubkey to `AuthorizeNonceAccount`, or (b) a wallet/tool bug that defaults an unset "new authority" field to `Pubkey::default()` (as literally seen being constructed/tested in the CLI test fixtures at [6](#0-5) , which shows `Pubkey::default()` flowing naturally through the CLI's parsed command path as a placeholder value). There is no external attacker path to trigger this against a victim's account.

### Recommendation
Add explicit validation in `authorize_nonce_account` and `initialize_nonce_account` (`programs/system/src/system_instruction.rs`) rejecting `Pubkey::default()` (and optionally warning on setting the authority to a program-owned/PDA-looking address without an accompanying capability to sign) before persisting the new authority into `nonce::state::Data`. This is analogous to the Trail of Bits recommendation to add an `address(0)` check and prefer explicit two-step confirmation for privileged role changes.

### Proof of Concept
1. Create and initialize a nonce account with authority `A` (signer).
2. `A` submits `SystemInstruction::AuthorizeNonceAccount(Pubkey::default())` signed by `A`; per [4](#0-3)  this succeeds and the nonce account's `authorized` field becomes `Pubkey::default()`.
3. Any subsequent `AuthorizeNonceAccount`, `AdvanceNonceAccount`, or `WithdrawNonceAccount` instruction against this account now requires a signature from `Pubkey::default()`, which no one can produce, permanently locking the nonce account's authority-gated functionality.

### Citations

**File:** programs/system/src/system_instruction.rs (L163-211)
```rust
pub(crate) fn initialize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    rent: &Rent,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Initialize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

    match account.get_state::<Versions>()?.state() {
        State::Uninitialized => {
            let min_balance = rent.minimum_balance(account.get_data().len());
            if account.get_lamports() < min_balance {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: insufficient lamports {}, need {}",
                    account.get_lamports(),
                    min_balance
                );
                return Err(InstructionError::InsufficientFunds);
            }
            let durable_nonce =
                DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
            let data = nonce::state::Data::new(
                *nonce_authority,
                durable_nonce,
                invoke_context
                    .environment_config
                    .blockhash_lamports_per_signature,
            );
            let state = State::Initialized(data);
            account.set_state(&Versions::new(state))
        }
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
    }
}
```

**File:** programs/system/src/system_instruction.rs (L213-249)
```rust
pub(crate) fn authorize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Authorize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }
    match account
        .get_state::<Versions>()?
        .authorize(signers, *nonce_authority)
    {
        Ok(versions) => account.set_state(&versions),
        Err(AuthorizeNonceError::Uninitialized) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
        Err(AuthorizeNonceError::MissingRequiredSignature(account_authority)) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} must sign",
                account_authority
            );
            Err(InstructionError::MissingRequiredSignature)
        }
    }
}
```

**File:** programs/system/src/system_instruction.rs (L1010-1038)
```rust
    #[test]
    fn authorize_inx_ok() {
        prepare_mockup!(
            invoke_context,
            instruction_accounts,
            rent,
            transaction_context
        );
        push_instruction_context!(invoke_context, instruction_context, instruction_accounts);
        let mut nonce_account = instruction_context
            .try_borrow_instruction_account(NONCE_ACCOUNT_INDEX)
            .unwrap();
        let mut signers = HashSet::new();
        signers.insert(*nonce_account.get_key());
        set_invoke_context_blockhash!(invoke_context, 31);
        let authorized = *nonce_account.get_key();
        initialize_nonce_account(&mut nonce_account, &authorized, &rent, &invoke_context).unwrap();
        let authority = Pubkey::default();
        let data = nonce::state::Data::new(
            authority,
            DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash),
            invoke_context
                .environment_config
                .blockhash_lamports_per_signature,
        );
        authorize_nonce_account(&mut nonce_account, &authority, &signers, &invoke_context).unwrap();
        let versions = nonce_account.get_state::<Versions>().unwrap();
        assert_eq!(versions.state(), &State::Initialized(data));
    }
```

**File:** programs/system/src/system_processor.rs (L468-472)
```rust
        SystemInstruction::AuthorizeNonceAccount(nonce_authority) => {
            instruction_context.check_number_of_instruction_accounts(1)?;
            let mut me = instruction_context.try_borrow_instruction_account(0)?;
            authorize_nonce_account(&mut me, &nonce_authority, &signers, invoke_context)
        }
```

**File:** cli/src/nonce.rs (L406-429)
```rust
pub async fn process_authorize_nonce_account(
    rpc_client: &RpcClient,
    config: &CliConfig<'_>,
    nonce_account: &Pubkey,
    nonce_authority: SignerIndex,
    memo: Option<&String>,
    new_authority: &Pubkey,
    compute_unit_price: Option<u64>,
) -> ProcessResult {
    let latest_blockhash = rpc_client.get_latest_blockhash().await?;

    let nonce_authority = config.signers[nonce_authority];
    let compute_unit_limit = ComputeUnitLimit::Simulated;
    let ixs = vec![authorize_nonce_account(
        nonce_account,
        &nonce_authority.pubkey(),
        new_authority,
    )]
    .with_memo(memo)
    .with_compute_unit_config(&ComputeUnitConfig {
        compute_unit_price,
        compute_unit_limit,
    });
    let mut message = Message::new(&ixs, Some(&config.signers[0].pubkey()));
```

**File:** cli/src/nonce.rs (L785-804)
```rust
        // Test AuthorizeNonceAccount Subcommand
        let test_authorize_nonce_account = test_commands.clone().get_matches_from(vec![
            "test",
            "authorize-nonce-account",
            &keypair_file,
            &Pubkey::default().to_string(),
        ]);
        assert_eq!(
            parse_command(&test_authorize_nonce_account, &default_signer, &mut None).unwrap(),
            CliCommandInfo {
                command: CliCommand::AuthorizeNonceAccount {
                    nonce_account: nonce_account_pubkey,
                    nonce_authority: 0,
                    memo: None,
                    new_authority: Pubkey::default(),
                    compute_unit_price: None,
                },
                signers: vec![Box::new(read_keypair_file(&default_keypair_file).unwrap())],
            }
        );
```
