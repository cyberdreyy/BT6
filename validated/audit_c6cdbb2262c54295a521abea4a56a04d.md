### Title
`InitializeBuffer` sets buffer authority to an unverified, unsigned account, allowing front-running griefing analogous to `Bribe.setGauge` - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `UpgradeableLoaderInstruction::InitializeBuffer` handler in the upgradeable BPF loader sets the buffer's authority from instruction account index 1 without requiring that account to be a signer, and without any check that the caller invoking `InitializeBuffer` is the same party that created/funded the buffer account via `system_instruction::create_account`. If buffer creation and buffer initialization are not submitted as a single atomic transaction, any third party can race to call `InitializeBuffer` on the now-owned-by-loader-but-uninitialized buffer account first, permanently claiming the authority slot and rendering the buffer account useless to its intended deployer — the same "setter callable once by anyone, in between two logically-linked steps" pattern described in the Velodrome `Bribe.setGauge` report.

### Finding Description
`InitializeBuffer` only checks that the buffer's current state is `Uninitialized` before writing the new state; it takes the authority address directly from `instruction_context.get_key_of_instruction_account(1)` with no signature requirement on that account and no signature requirement on the buffer account itself: [1](#0-0) 

This differs from every other privileged instruction in the same file (`Write`, `DeployWithMaxDataLen`, `SetAuthority`, `SetAuthorityChecked`), all of which explicitly verify `is_instruction_account_signer` for the authority before mutating state: [2](#0-1) [3](#0-2) 

The buffer account only becomes writable/settable by the loader once its owner has been reassigned to `bpf_loader_upgradeable::id()` (typically via a `system_instruction::create_account` transaction). Once that ownership transfer has landed on-chain, the account sits in the `Uninitialized` loader state until `InitializeBuffer` is called. If the deployer's `CreateAccount` and `InitializeBuffer` calls are not bundled into the same transaction (i.e., not executed atomically), there is a window where any unprivileged party can submit their own `InitializeBuffer` instruction against the same buffer pubkey, setting themselves (or any arbitrary pubkey they choose in slot 1) as `authority_address` — because slot 1 is never required to be a signer.

This is structurally identical to the Velodrome bug: `Bribe.setGauge` was `external`, callable by anyone exactly once, with no atomicity guarantee versus the `Bribe` constructor; here, `InitializeBuffer` is likewise callable by anyone exactly once (guarded only by the `Uninitialized` state check), with no atomicity guarantee versus the preceding `CreateAccount` call unless the client explicitly composes them into one transaction.

### Impact Explanation
If a deployer relies on separate transactions for `CreateAccount` + `InitializeBuffer` (e.g., due to retries, multi-step tooling, or non-atomic workflows), an attacker monitoring the mempool/leader schedule can front-run the `InitializeBuffer` call and set an authority address of their choosing on the buffer. This does not let the attacker steal funds directly (the buffer is otherwise still owned by the legitimate creator's lamports), but it permanently denies the intended deployer control over `Write`/`SetAuthority` on that buffer, since all subsequent state changes require the (now attacker-controlled) authority to sign. The buffer becomes unusable for its intended program deployment, forcing the deployer to abandon the account and lose the rent-exempt lamports already deposited — a temporary, targeted denial-of-service/griefing of the deploy pipeline, not a cluster-wide or consensus-level failure.

### Likelihood Explanation
Likelihood is low-to-moderate in practice: the reference CLI (`cli/src/program.rs`) and SDK helpers construct `CreateAccount` + `InitializeBuffer` as a single transaction by convention, which closes this window for typical `solana program deploy` usage. However, the on-chain instruction handler itself provides no enforced atomicity or signer check, so any custom tooling, wallet, or program that issues these as separate transactions (e.g., to control fee payment, batching, or retries) is exposed. This mirrors exactly the Velodrome finding's root cause: the vulnerability exists at the protocol/instruction level and is only mitigated by disciplined off-chain composition, not by an on-chain guarantee.

### Recommendation
Add a signer check on the authority account (index 1) in `InitializeBuffer`, analogous to what `Write`/`SetAuthority`/`SetAuthorityChecked` already do, so that only a party who can produce a valid signature for the intended authority can claim it:
```rust
if !instruction_context.is_instruction_account_signer(1)? {
    return Err(InstructionError::MissingRequiredSignature);
}
```
Additionally/alternatively, require the buffer account itself (index 0) to be a signer on `InitializeBuffer`, which — combined with requiring `CreateAccount` and `InitializeBuffer` to reference the same freshly-generated keypair — forces the two steps to be authorized by the same party that generated the buffer keypair, closing the front-running window even when the two instructions land in different transactions.

### Proof of Concept
1. Deployer submits transaction A: `system_instruction::create_account(payer, buffer_pubkey, lamports, size, bpf_loader_upgradeable::id())`, transferring ownership of `buffer_pubkey` to the loader; the account state defaults to `Uninitialized`.
2. Before the deployer's follow-up transaction lands, an attacker observes `buffer_pubkey` on-chain in `Uninitialized` state and submits `UpgradeableLoaderInstruction::InitializeBuffer` with instruction accounts `[buffer_pubkey (writable, non-signer), attacker_pubkey (non-signer)]`.
3. The handler at [1](#0-0)  only checks `Uninitialized != buffer.get_state()?` and then unconditionally sets `authority_address = Some(attacker_pubkey)` — no signature is checked on either account.
4. The deployer's original `InitializeBuffer` call (intending to set their own authority) now fails with `AccountAlreadyInitialized`, and all subsequent legitimate `Write`/`SetAuthority` calls fail `IncorrectAuthority`/`MissingRequiredSignature` checks against the attacker's key, permanently griefing the buffer account.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L158-172)
```rust
        UpgradeableLoaderInstruction::InitializeBuffer => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut buffer = instruction_context.try_borrow_instruction_account(0)?;

            if UpgradeableLoaderState::Uninitialized != buffer.get_state()? {
                ic_logger_msg!(log_collector, "Buffer account already initialized");
                return Err(InstructionError::AccountAlreadyInitialized);
            }

            let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);

            buffer.set_state(&UpgradeableLoaderState::Buffer {
                authority_address: authority_key,
            })?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L549-576)
```rust
        UpgradeableLoaderInstruction::SetAuthority => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut account = instruction_context.try_borrow_instruction_account(0)?;
            let present_authority_key = instruction_context.get_key_of_instruction_account(1)?;
            let new_authority = instruction_context.get_key_of_instruction_account(2).ok();

            match account.get_state()? {
                UpgradeableLoaderState::Buffer { authority_address } => {
                    if new_authority.is_none() {
                        ic_logger_msg!(log_collector, "Buffer authority is not optional");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Buffer is immutable");
                        return Err(InstructionError::Immutable);
                    }
                    if authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Buffer authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    account.set_state(&UpgradeableLoaderState::Buffer {
                        authority_address: new_authority.cloned(),
                    })?;
                }
```

**File:** programs/bpf_loader/src/lib.rs (L618-652)
```rust
        UpgradeableLoaderInstruction::SetAuthorityChecked => {
            if !invoke_context
                .get_feature_set()
                .enable_bpf_loader_set_authority_checked_ix
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(3)?;
            let mut account = instruction_context.try_borrow_instruction_account(0)?;
            let present_authority_key = instruction_context.get_key_of_instruction_account(1)?;
            let new_authority_key = instruction_context.get_key_of_instruction_account(2)?;

            match account.get_state()? {
                UpgradeableLoaderState::Buffer { authority_address } => {
                    if authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Buffer is immutable");
                        return Err(InstructionError::Immutable);
                    }
                    if authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Buffer authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    if !instruction_context.is_instruction_account_signer(2)? {
                        ic_logger_msg!(log_collector, "New authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    account.set_state(&UpgradeableLoaderState::Buffer {
                        authority_address: Some(*new_authority_key),
                    })?;
                }
```
