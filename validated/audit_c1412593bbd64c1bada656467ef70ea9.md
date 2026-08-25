[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** precompiles/src/lib.rs (L34-41)
```rust
    pub fn check_id<F>(&self, program_id: &Pubkey, is_enabled: F) -> bool
    where
        F: Fn(&Pubkey) -> bool,
    {
        self.feature
            .is_none_or(|ref feature_id| is_enabled(feature_id))
            && self.program_id == *program_id
    }
```

**File:** precompiles/src/lib.rs (L104-118)
```rust
) -> Result<(), PrecompileError> {
    for precompile in PRECOMPILES.iter() {
        if precompile.check_id(program_id, |feature_id| feature_set.is_active(feature_id)) {
            let instruction_datas: Vec<_> = all_instructions
                .iter()
                .map(|instruction| instruction.data.as_ref())
                .collect();
            return precompile.verify(
                &precompile_instruction.data,
                &instruction_datas,
                feature_set,
            );
        }
    }
    Ok(())
```

**File:** runtime/src/bank.rs (L6312-6318)
```rust
        for precompile in get_precompiles() {
            if let Some(feature_id) = &precompile.feature
                && new_feature_activations.contains(feature_id)
            {
                self.add_precompile(&precompile.program_id);
            }
        }
```
