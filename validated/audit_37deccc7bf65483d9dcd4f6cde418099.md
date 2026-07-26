[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/party.move (L50-54)
```text
public fun single_owner(owner: address): Party {
    let mut mp = empty();
    mp.set_permissions(owner, Permissions(ALL_PERMISSIONS));
    mp
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/transfer.move (L91-95)
```text
public fun public_party_transfer<T: key + store>(obj: T, party: sui::party::Party) {
    assert!(party.is_single_owner(), EInvalidPartyPermissions);
    let (default, addresses, permissions) = party.into_native();
    party_transfer_impl(obj, default, addresses, permissions)
}
```

**File:** sui-execution/latest/sui-move-natives/src/transfer.rs (L224-250)
```rust
    let Ok([permissions]): Result<[u64; 1], _> = permissions.try_into() else {
        return Err(
            PartialVMError::new(StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR)
                .with_message("Party transfer only supports one party member".to_string()),
        );
    };
    if permissions != ALL || default_permissions != NONE {
        return Err(
            PartialVMError::new(StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR).with_message(
                "Party transfer only supports one party member with all permissions".to_string(),
            ),
        );
    }
    let Ok([address]): Result<[AccountAddress; 1], _> = addresses.try_into() else {
        return Err(
            PartialVMError::new(StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR)
                .with_message("Party transfer only supports one party member".to_string()),
        );
    };

    // Dummy version, to be filled with the correct initial version when the effects of the
    // transaction are written to storage.
    let owner = Owner::ConsensusAddressOwner {
        start_version: SequenceNumber::new(),
        owner: address.into(),
    };
    object_runtime_transfer(context, owner, ty, obj)?;
```

**File:** crates/sui-transaction-checks/src/lib.rs (L669-686)
```rust
                    Owner::ConsensusAddressOwner {
                        start_version: actual_initial_shared_version,
                        owner: actual_owner,
                    } => {
                        fp_ensure!(
                            input_initial_shared_version == *actual_initial_shared_version,
                            UserInputError::SharedObjectStartingVersionMismatch
                        );
                        // Check the owner is correct.
                        fp_ensure!(
                            owner == actual_owner,
                            UserInputError::IncorrectUserSignature {
                                error: format!(
                                    "Object {object_id:?} is owned by account address {actual_owner:?}, but given owner/signer address is {owner:?}"
                                ),
                            }
                        )
                    }
```

**File:** crates/sui-e2e-tests/tests/party_objects_tests.rs (L228-234)
```rust
    assert_eq!(
        mutated_party.1,
        Owner::ConsensusAddressOwner {
            start_version: object_initial_shared_version.next(),
            owner: SuiAddress::ZERO,
        }
    );
```
