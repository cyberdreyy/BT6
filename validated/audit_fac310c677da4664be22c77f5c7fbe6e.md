### Title
Unauthenticated `InitializeBuffer` lets anyone hijack the authority of a not-yet-initialized upgradeable-loader buffer account - (File: programs/bpf_loader/src/lib.rs)

### Summary
The `bpf_loader_upgradeable` program's `InitializeBuffer` instruction handler sets a buffer account's `authority_address` to whatever pubkey is supplied as instruction account #1, without requiring that key to sign the transaction and without requiring the buffer account itself (account #0) to sign. Any transaction sender who knows the address of a writable, uninitialized buffer account owned by `bpf_loader_upgradeable` can call `InitializeBuffer` and claim (or assign to any arbitrary key) the authority over that buffer, exactly analogous to the reported `PoolOracle.initialize()` bug where any caller becomes the "owner" because the initializer has no access control.

### Finding Description
`process_loader_upgradeable_instruction` handles `UpgradeableLoaderInstruction::InitializeBuffer` as follows: [1](#0-0) 

It only checks that the buffer account's state is `Uninitialized` (`AccountAlreadyInitialized` guard) and then unconditionally sets `authority_address` to the key found at instruction-account index 1. There is no `is_instruction_account_signer` check on either account 0 (the buffer) or account 1 (the prospective authority) — contrast this with `Write`, `Upgrade`, and `SetAuthority`, which explicitly call `instruction_context.is_instruction_account_signer(...)` and return `InstructionError::MissingRequiredSignature` if the authority did not sign, e.g. in `SetAuthority`: [2](#0-1) 

The unit test for `InitializeBuffer` even documents this by setting `is_signer: false` for both the buffer and the authority account metas: [3](#0-2) 

Because the buffer account only needs to already exist, be owned by `bpf_loader_upgradeable`, and be in the `Uninitialized` state (a state trivially reached right after `SystemProgram::CreateAccount` assigns ownership to the loader, before the legitimate owner's `InitializeBuffer` instruction lands), any third party who observes the buffer pubkey (e.g. from the mempool, from an earlier transaction, or by brute-force guessing a known/derived address) can race to submit their own `InitializeBuffer` naming themselves as `authority_address`. The legitimate creator's follow-up `InitializeBuffer` will then fail with `AccountAlreadyInitialized`, and the attacker now holds `authority_address`.

### Impact Explanation
Holding an illegitimate `authority_address` over the buffer grants control equivalent to `PoolOracle`'s hijacked `owner`:
- `Write` requires only that the authority in the buffer state matches the signer — the attacker as new "authority" can overwrite buffer contents (though this only matters before deployment).
- `SetAuthority`/`SetAuthorityChecked` let the attacker transfer/finalize authority as they see fit.
- Deployment via `DeployWithMaxDataLen`/`Upgrade` checks that the caller-supplied authority key matches the buffer's stored `authority_address` and is a signer — so an attacker holding buffer authority can block or hijack legitimate program deployment flows that depend on that specific buffer, and can also close the buffer account to redirect its lamports to an account of the attacker's choosing via the `Close` instruction (which requires only the stored authority to sign, not the original funder).

This does not compromise consensus or violate lamport conservation (lamports move only where the account's declared authority permits, which is exactly the vulnerability — the "declared authority" itself is unauthenticated), but it is a concrete authority/ownership-hijack primitive reachable by any unprivileged transaction sender, directly analogous to the reported bug class ("anyone can become the owner").

### Likelihood Explanation
Moderate. Exploitation requires the attacker to submit `InitializeBuffer` before the legitimate creator's own `InitializeBuffer` transaction is committed (a front-running / race condition), or to target buffer accounts created and left uninitialized for any period. In practice tooling (CLI `program deploy`) creates and initializes the buffer within the same atomic transaction/signed batch, which reduces the exploitation window, but nothing in the on-chain instruction logic prevents a separate, unsigned `InitializeBuffer` call from claiming an existing uninitialized buffer account whenever such a window exists (e.g., custom tooling, multi-step deployments, or buffer accounts pre-funded and left pending).

### Recommendation
Require `instruction_context.is_instruction_account_signer(1)` (the intended authority) to sign in `InitializeBuffer`, mirroring the signer checks already present in `Write`, `Upgrade`, and `SetAuthority`. Optionally also require the buffer account (account 0) to be a signer, consistent with how newly created accounts are normally authenticated in the same transaction that creates them.

### Proof of Concept
1. Attacker (or anyone) observes a `bpf_loader_upgradeable`-owned account `B` in the `Uninitialized` state (e.g., freshly created via `SystemProgram::CreateAccount` with `owner = bpf_loader_upgradeable`, before the creator's `InitializeBuffer` transaction lands).
2. Attacker submits a transaction with a single `InitializeBuffer` instruction:
   - account 0 = `B` (writable, `is_signer: false`)
   - account 1 = attacker's own pubkey `A` (`is_signer: false` — no signature required)
3. `process_loader_upgradeable_instruction` sees `B`'s state as `Uninitialized`, passes the check, and sets `B`'s state to `Buffer { authority_address: Some(A) }` per [4](#0-3) .
4. The legitimate creator's subsequent `InitializeBuffer` transaction for `B` now fails with `AccountAlreadyInitialized`.
5. Attacker, as the recorded `authority_address`, can now call `SetAuthority`, `Write`, or `Close` on `B`, controlling or destroying the buffer and redirecting any lamports held in it.

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

**File:** programs/bpf_loader/src/lib.rs (L565-572)
```rust
                    if authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Buffer authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
```

**File:** programs/bpf_loader/src/lib.rs (L1408-1441)
```rust
    fn test_bpf_loader_upgradeable_initialize_buffer() {
        let loader_id = bpf_loader_upgradeable::id();
        let buffer_address = Pubkey::new_unique();
        let buffer_account =
            AccountSharedData::new(1, UpgradeableLoaderState::size_of_buffer(9), &loader_id);
        let authority_address = Pubkey::new_unique();
        let authority_account =
            AccountSharedData::new(1, UpgradeableLoaderState::size_of_buffer(9), &loader_id);
        let instruction_data =
            bincode::serialize(&UpgradeableLoaderInstruction::InitializeBuffer).unwrap();
        let instruction_accounts = vec![
            AccountMeta {
                pubkey: buffer_address,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: authority_address,
                is_signer: false,
                is_writable: false,
            },
        ];

        // Case: Success
        let accounts = process_instruction(
            &loader_id,
            &instruction_data,
            vec![
                (buffer_address, buffer_account),
                (authority_address, authority_account),
            ],
            instruction_accounts.clone(),
            Ok(()),
        );
```
