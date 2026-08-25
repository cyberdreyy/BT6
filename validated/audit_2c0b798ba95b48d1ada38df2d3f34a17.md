### Title
Unauthenticated `InitializeBuffer` Allows Front-Running of Program Deploy Buffer Accounts - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `bpf_loader_upgradeable` program's `InitializeBuffer` instruction handler sets a buffer account's authority to whatever pubkey is supplied at instruction-account index 1, without requiring that account to be a transaction signer and without any check that the caller is the entity that created the buffer account. [1](#0-0)  Because program deployment via the CLI is a multi-transaction process — first creating the buffer account owned by the loader (left in `UpgradeableLoaderState::Uninitialized`), then separately submitting `InitializeBuffer` — there is a window, analogous to the Rocket Pool `RocketStorage` deployment bug, during which any third party can observe the (publicly reported/ephemeral) buffer pubkey and submit their own `InitializeBuffer` transaction first, claiming the buffer authority for themselves and blocking the legitimate deployer.

### Finding Description
`process_loader_upgradeable_instruction` handles `UpgradeableLoaderInstruction::InitializeBuffer` as follows: it only requires that the target account currently be `Uninitialized`, then unconditionally writes `UpgradeableLoaderState::Buffer { authority_address: authority_key }` where `authority_key` is simply read from instruction-account index 1 — with no `is_instruction_account_signer` check on that account, and no check that the caller (transaction fee payer / signer) has any relationship to the account that created the buffer. [1](#0-0) 

This mirrors the Rocket Pool report's root cause: deployment is not atomic (the CLI's `program deploy` flow creates the buffer account in one transaction via `system_instruction::create_account`, then submits `InitializeBuffer` in a separate transaction, reporting the buffer pubkey/ephemeral mnemonic to allow resuming after a crash). [2](#0-1)  Between account creation and the `InitializeBuffer` call, the buffer account sits on-chain, owned by `bpf_loader_upgradeable`, in state `Uninitialized`, with its address publicly visible in the create-account transaction or reported ephemeral mnemonic. Any account can then submit its own `InitializeBuffer` instruction referencing that buffer address (as a non-signing, writable account) and an attacker-controlled authority key, which succeeds because the check is only `state == Uninitialized`, not "was created by me" or "authority account signed."

Once the attacker's `InitializeBuffer` transaction lands first, the account's state becomes `Buffer { authority_address: Some(attacker_key) }`. The legitimate deployer's subsequent `InitializeBuffer` transaction then fails with `AccountAlreadyInitialized`, and any later `Write`/`DeployWithMaxDataLen`/`Upgrade` attempts by the deployer will fail authority checks (`IncorrectAuthority`) since those instructions correctly verify the recorded buffer authority against a signer. [3](#0-2) [4](#0-3) 

### Impact Explanation
This is a griefing/denial-of-service vector on ordinary program deployment: any unprivileged network observer can hijack a not-yet-initialized buffer account (whose address becomes public once the create-account transaction is broadcast or the ephemeral mnemonic is reported) and permanently claim its authority before the intended deployer's `InitializeBuffer` transaction lands, forcing the deployer to abandon that buffer and retry with a new one, wasting the rent-exempt lamports already paid into the account and costing repeated gas/compute on failed deploy attempts. Unlike the Rocket Pool `rocketVault` scenario, this analog does not directly enable fund theft, because downstream `Write`/`Deploy`/`Upgrade` instructions still verify that the caller's supplied authority key matches the account's recorded authority and that it is a signer — so an attacker cannot ultimately push malicious bytecode into a program the victim believes they control. The concrete, provable impact is unauthorized state mutation of another party's account (authority hijack) and denial-of-service against the deployment flow, not loss of already-deployed program funds.

### Likelihood Explanation
Likelihood is moderate: exploitation only requires observing a buffer account's pubkey in a pending or confirmed create-account transaction (or via the CLI's explicit ephemeral-mnemonic reporting for crash recovery) and racing a single, cheap `InitializeBuffer` transaction against the legitimate deployer's next transaction. No front-running with elevated gas/priority fees is strictly required if the attacker simply reacts quickly after the create-account transaction confirms, though faster/most reliable exploitation would use priority fees or mempool monitoring, similar to the "targeted attacker monitoring the mempool" scenario described in the original report.

### Recommendation
Require that `InitializeBuffer` only succeed when the buffer account itself is a signer of the transaction (proving control of the buffer's private key, which is generated fresh per-deploy and known only to the legitimate deployer at creation time), or alternatively require that account 1 (the intended authority) be a signer of the instruction before recording it as the buffer authority. This closes the window during which an unrelated account can claim authority over a not-yet-initialized buffer, mirroring the RocketStorage fix of requiring the initializing party to be authenticated rather than relying solely on an "uninitialized" state check.

### Proof of Concept
1. Victim submits transaction A: `system_instruction::create_account` creating buffer pubkey `B` (owner = `bpf_loader_upgradeable`, empty/zeroed data ⇒ state `Uninitialized`), funded with rent-exempt lamports.
2. Attacker observes `B` in the confirmed transaction A (or via the CLI's reported ephemeral mnemonic for buffer `B`).
3. Attacker submits transaction X: `UpgradeableLoaderInstruction::InitializeBuffer` with instruction accounts `[B, attacker_authority_key]`, where `B` is *not* a signer and `attacker_authority_key` is *not* a signer either — this is accepted because the handler only checks `buffer.get_state()? == Uninitialized`. [1](#0-0) 
4. Account `B`'s state becomes `Buffer { authority_address: Some(attacker_authority_key) }`.
5. Victim's own transaction B (`InitializeBuffer` on `B` with their intended authority) now fails with `InstructionError::AccountAlreadyInitialized`, and any subsequent `Write`/`Deploy` attempts by the victim fail authority checks — the deploy is effectively blocked and the victim's rent-exempt deposit into `B` is stranded until they reclaim/close it and start over with a new buffer.

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

**File:** programs/bpf_loader/src/lib.rs (L173-190)
```rust
        UpgradeableLoaderInstruction::Write { offset, bytes } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let buffer = instruction_context.try_borrow_instruction_account(0)?;

            if let UpgradeableLoaderState::Buffer { authority_address } = buffer.get_state()? {
                if authority_address.is_none() {
                    ic_logger_msg!(log_collector, "Buffer is immutable");
                    return Err(InstructionError::Immutable); // TODO better error code
                }
                let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(1)? {
                    ic_logger_msg!(log_collector, "Buffer authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L242-250)
```rust
            if let UpgradeableLoaderState::Buffer { authority_address } = buffer.get_state()? {
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Buffer and upgrade authority don't match");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(7)? {
                    ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
```

**File:** cli/src/program.rs (L1449-1453)
```rust
    if !buffer_provided {
        // always report ephemeral mnemonic, so that users always have a way to resume in case of
        // process crash
        report_ephemeral_mnemonic(buffer_words, buffer_mnemonic, &buffer_pubkey);
    }
```
