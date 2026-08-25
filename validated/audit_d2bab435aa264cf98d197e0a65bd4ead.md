[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** programs/vote/src/vote_processor.rs (L36-50)
```rust
    let clock = get_sysvar_with_account_check::clock(invoke_context, instruction_context, 1)?;
    let mut expected_authority_keys: HashSet<Pubkey> = HashSet::default();
    if instruction_context.is_instruction_account_signer(2)? {
        let base_pubkey = instruction_context.get_key_of_instruction_account(2)?;
        // The conversion from `PubkeyError` to `InstructionError` through
        // num-traits is incorrect, but it's the existing behavior.
        expected_authority_keys.insert(
            Pubkey::create_with_seed(
                base_pubkey,
                current_authority_derived_key_seed,
                current_authority_derived_key_owner,
            )
            .map_err(|e| e as u64)?,
        );
    };
```

**File:** programs/vote/src/vote_processor.rs (L3537-3554)
```rust
        // Can't change authority unless base key signs.
        instruction_accounts[2].is_signer = false;
        process_instruction_with_cu_check(
            features,
            &serialize(&VoteInstruction::AuthorizeWithSeed(
                VoteAuthorizeWithSeedArgs {
                    authorization_type,
                    current_authority_derived_key_owner: current_authority_owner,
                    current_authority_derived_key_seed: current_authority_seed.clone(),
                    new_authority: new_authority_pubkey,
                },
            ))
            .unwrap(),
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Err(InstructionError::MissingRequiredSignature),
            expected_cus,
        );
```

**File:** programs/vote/src/vote_processor.rs (L3557-3591)
```rust
        // Can't change authority if seed doesn't match.
        process_instruction_with_cu_check(
            features,
            &serialize(&VoteInstruction::AuthorizeWithSeed(
                VoteAuthorizeWithSeedArgs {
                    authorization_type,
                    current_authority_derived_key_owner: current_authority_owner,
                    current_authority_derived_key_seed: String::from("WRONG_SEED"),
                    new_authority: new_authority_pubkey,
                },
            ))
            .unwrap(),
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Err(InstructionError::MissingRequiredSignature),
            expected_cus,
        );

        // Can't change authority if owner doesn't match.
        process_instruction_with_cu_check(
            features,
            &serialize(&VoteInstruction::AuthorizeWithSeed(
                VoteAuthorizeWithSeedArgs {
                    authorization_type,
                    current_authority_derived_key_owner: Pubkey::new_unique(), // Wrong owner.
                    current_authority_derived_key_seed: current_authority_seed.clone(),
                    new_authority: new_authority_pubkey,
                },
            ))
            .unwrap(),
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Err(InstructionError::MissingRequiredSignature),
            expected_cus,
        );
```

**File:** transaction-context/src/instruction.rs (L148-183)
```rust
    /// Get the index of account in instruction from the index in transaction
    pub fn get_index_of_account_in_instruction(
        &self,
        index_in_transaction: IndexOfAccount,
    ) -> Result<IndexOfAccount, InstructionError> {
        self.dedup_map
            .get(index_in_transaction as usize)
            .and_then(|idx| {
                if *idx as usize >= self.instruction_accounts.len() {
                    None
                } else {
                    Some(*idx as IndexOfAccount)
                }
            })
            .ok_or(InstructionError::MissingAccount)
    }

    /// Returns `Some(instruction_account_index)` if this is a duplicate
    /// and `None` if it is the first account with this key
    pub fn is_instruction_account_duplicate(
        &self,
        instruction_account_index: IndexOfAccount,
    ) -> Result<Option<IndexOfAccount>, InstructionError> {
        let index_in_transaction =
            self.get_index_of_instruction_account_in_transaction(instruction_account_index)?;
        let first_instruction_account_index =
            self.get_index_of_account_in_instruction(index_in_transaction)?;

        Ok(
            if first_instruction_account_index == instruction_account_index {
                None
            } else {
                Some(first_instruction_account_index)
            },
        )
    }
```

**File:** transaction-context/src/instruction.rs (L229-238)
```rust
    pub fn is_instruction_account_signer(
        &self,
        instruction_account_index: IndexOfAccount,
    ) -> Result<bool, InstructionError> {
        Ok(self
            .instruction_accounts
            .get(instruction_account_index as usize)
            .ok_or(InstructionError::MissingAccount)?
            .is_signer())
    }
```

**File:** transaction-context/src/instruction_accounts.rs (L19-26)
```rust
pub struct InstructionAccount {
    /// Points to the account and its key in the `TransactionContext`
    pub index_in_transaction: IndexOfAccount,
    /// Is this account supposed to sign
    is_signer: u8,
    /// Is this account allowed to become writable
    is_writable: u8,
}
```
