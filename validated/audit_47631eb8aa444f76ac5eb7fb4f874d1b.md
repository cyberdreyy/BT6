### Title
`InitializeNonceAccount` can be frontrun to seize control of a pre-funded nonce account - (File: `programs/system/src/system_instruction.rs`)

### Summary
The System Program's `InitializeNonceAccount` instruction sets the nonce authority to whatever pubkey is supplied in the instruction, without requiring that pubkey (or any other party) to co-sign or otherwise prove a relationship to the account that funded/created the nonce account. Because account creation (`CreateAccount`) and nonce initialization are two separate instructions that a wallet/dApp is expected to bundle atomically, an attacker who observes the `CreateAccount` transaction for a nonce account can race ahead of the legitimate owner's `InitializeNonceAccount` transaction and become the nonce authority themselves, later draining the lamports via `WithdrawNonceAccount`.

### Finding Description
`initialize_nonce_account` only validates that the account is writable, currently `Uninitialized`, and rent-exempt; it never checks that the caller (or the supplied `nonce_authority`) is a signer of the account being initialized: [1](#0-0) 

The `nonce_authority` argument comes straight from instruction data with no signature or ownership constraint tying it to the account or its original funder. Once initialized, `withdraw_nonce_account` and `advance_nonce_account` both authorize solely based on whoever is recorded as `data.authority`: [2](#0-1) [3](#0-2) 

This mirrors the `createProject` frontrun bug class: a state-establishing call (`CoreFactory.createProject` → becomes project owner; here, `InitializeNonceAccount` → becomes nonce authority) is unauthenticated with respect to the resource's intended owner, so whoever's transaction lands first on an already-existing/funded resource claims control of it.

### Impact Explanation
If a user (or wallet software) submits `CreateAccount` (funding a new nonce account, rent-exempt lamports included) and `InitializeNonceAccount` as separate transactions instead of atomically bundled in one transaction, an attacker monitoring the network for the `CreateAccount` transaction can submit their own `InitializeNonceAccount` instruction against that same account address first, setting themselves (or any pubkey they control) as `nonce_authority`. Once they hold the authority, they can call `WithdrawNonceAccount` to drain all lamports funded into the account (since withdrawal authorization checks only `data.authority`, not the original creator), resulting in concrete unauthorized fund loss.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to submit `CreateAccount` and `InitializeNonceAccount` as two separate transactions (rather than the single atomic transaction that `solana_system_interface::instruction::create_nonce_account` normally constructs), and it requires an attacker to observe the intervening state and race a transaction ahead of the victim's initialization instruction. This is a well-known Solana nonce-account footgun rather than a newly introduced defect, but the code path itself provides no defense-in-depth (e.g., requiring the funder or a designated base key to co-sign initialization), so any caller who does not strictly bundle the two instructions atomically is exposed.

### Recommendation
- Document/enforce that `CreateAccount` and `InitializeNonceAccount` must always be submitted in the same atomic transaction (already the recommended SDK helper pattern) and audit any client code paths that split them.
- Consider requiring that the account being initialized be a signer of the `InitializeNonceAccount` instruction (analogous to `Allocate`/`Assign`, which require the target address to sign), so that only the party who controls the account's private key (typically the same party who created it) can set its initial authority.

### Proof of Concept
1. Victim submits `SystemInstruction::CreateAccount` funding a fresh keypair `N` with rent-exempt lamports, owned by the system program, sized for `NonceVersions`.
2. Before the victim submits their intended `SystemInstruction::InitializeNonceAccount { nonce_authority: victim_authority }` for `N`, attacker observes `N`'s creation and submits their own `InitializeNonceAccount { nonce_authority: attacker_pubkey }` referencing the same writable account `N`. Per `initialize_nonce_account`, no signature from `N` or any authority is checked—only that `N` is writable, uninitialized, and rent-exempt—so this succeeds and sets `authority = attacker_pubkey`. [4](#0-3) 
3. Attacker then submits `SystemInstruction::WithdrawNonceAccount` signing as `attacker_pubkey`, which passes the `check_signer(&data.authority)` check and transfers all lamports out of `N` to an account of the attacker's choosing. [3](#0-2)

### Citations

**File:** programs/system/src/system_instruction.rs (L41-49)
```rust
        State::Initialized(data) => {
            if !signers.contains(&data.authority) {
                ic_msg!(
                    invoke_context,
                    "Advance nonce account: Account {} must be a signer",
                    data.authority
                );
                return Err(InstructionError::MissingRequiredSignature);
            }
```

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

**File:** programs/system/src/system_instruction.rs (L163-201)
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
```
