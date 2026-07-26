[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/sui-types/src/object.rs (L928-965)
```rust
#[derive(
    Eq, PartialEq, Debug, Clone, Deserialize, Serialize, Hash, JsonSchema, Ord, PartialOrd,
)]
#[cfg_attr(feature = "fuzzing", derive(proptest_derive::Arbitrary))]
pub enum Owner {
    /// Object is exclusively owned by a single address, and is mutable.
    AddressOwner(SuiAddress),
    /// Object is exclusively owned by a single object, and is mutable.
    /// The object ID is converted to SuiAddress as SuiAddress is universal.
    ObjectOwner(SuiAddress),
    /// Object is shared, can be used by any address, and is mutable.
    Shared {
        /// The version at which the object became shared
        initial_shared_version: SequenceNumber,
    },
    /// Object is immutable, and hence ownership doesn't matter.
    Immutable,
    /// Object is exclusively owned by a single address and sequenced via consensus.
    ConsensusAddressOwner {
        /// The version at which the object most recently became a consensus object.
        /// This serves the same function as `initial_shared_version`, except it may change
        /// if the object's Owner type changes.
        start_version: SequenceNumber,
        // The owner of the object.
        owner: SuiAddress,
    },
    /// Object is sequenced via consensus with per-address permissions.
    /// Each address can be granted a subset of [`ObjectPermission`] flags.
    /// Addresses not explicitly listed fall back to the default permissions.
    Party {
        /// The version at which the object most recently became a party object.
        /// Serves the same function as `initial_shared_version`, except it may change
        /// if the object's Owner type changes.
        start_version: SequenceNumber,
        /// The permission map for this object.
        permissions: Party,
    },
}
```

**File:** crates/sui-types/src/object.rs (L967-1030)
```rust
impl Owner {
    // NOTE: only return address of AddressOwner, otherwise return error,
    // ObjectOwner's address is converted from object id, thus we will skip it.
    pub fn get_address_owner_address(&self) -> SuiResult<SuiAddress> {
        match self {
            Self::AddressOwner(address) => Ok(*address),
            Self::Shared { .. }
            | Self::Immutable
            | Self::ObjectOwner(_)
            | Self::ConsensusAddressOwner { .. }
            | Self::Party { .. } => Err(SuiErrorKind::UnexpectedOwnerType.into()),
        }
    }

    // NOTE: this function will return address of AddressOwner, ConsensusAddressOwner, and
    // ObjectOwner. The address of ObjectOwner is converted from object ID, even though the
    // type is SuiAddress.
    pub fn get_owner_address(&self) -> SuiResult<SuiAddress> {
        match self {
            Self::AddressOwner(address)
            | Self::ObjectOwner(address)
            | Self::ConsensusAddressOwner { owner: address, .. } => Ok(*address),
            Self::Shared { .. } | Self::Immutable | Self::Party { .. } => {
                Err(SuiErrorKind::UnexpectedOwnerType.into())
            }
        }
    }

    // Returns initial_shared_version for Shared objects, and start_version
    // for ConsensusAddressOwner and Party objects.
    pub fn start_version(&self) -> Option<SequenceNumber> {
        match self {
            Self::Shared {
                initial_shared_version,
            } => Some(*initial_shared_version),
            Self::ConsensusAddressOwner { start_version, .. }
            | Self::Party { start_version, .. } => Some(*start_version),
            Self::Immutable | Self::AddressOwner(_) | Self::ObjectOwner(_) => None,
        }
    }

    pub fn is_immutable(&self) -> bool {
        matches!(self, Owner::Immutable)
    }

    pub fn is_address_owned(&self) -> bool {
        matches!(self, Owner::AddressOwner(_))
    }

    pub fn is_child_object(&self) -> bool {
        matches!(self, Owner::ObjectOwner(_))
    }

    pub fn is_shared(&self) -> bool {
        matches!(self, Owner::Shared { .. })
    }

    pub fn is_consensus(&self) -> bool {
        matches!(
            self,
            Owner::Shared { .. } | Owner::ConsensusAddressOwner { .. } | Owner::Party { .. }
        )
    }
}
```

**File:** sui-execution/latest/sui-adapter/src/temporary_store/invariants.rs (L680-746)
```rust
        while let Some(to_authenticate) = objects_to_authenticate.pop() {
            if authenticated_for_mutation.contains(&to_authenticate.into()) {
                // object has already been authenticated
                continue;
            }

            let parent = if let Some(container_id) =
                store.wrapped_object_containers.get(&to_authenticate)
            {
                // It's a wrapped object, so check that the container is authenticated
                *container_id
            } else {
                // It's non-wrapped, so check the owner -- we can load the object from the
                // store.
                let Some(old_obj) = store.store.get_object(&to_authenticate) else {
                    panic!(
                        "Failed to load object {to_authenticate:?}.\n \
                         If it cannot be loaded, we would expect it to be in the wrapped object map: {:#?}",
                        &store.wrapped_object_containers
                    )
                };

                match &old_obj.owner {
                    // We mutated a dynamic field, we can continue to trace this back to verify
                    // proper ownership.
                    Owner::ObjectOwner(parent) => ObjectID::from(*parent),
                    // We mutated an address owned or sequenced address owned object -- one of two cases apply:
                    // 1) the object is owned by an object or address in the authenticated set,
                    // 2) the object is owned by some other address, in which case we should
                    //    continue to trace this back.
                    Owner::AddressOwner(parent)
                    | Owner::ConsensusAddressOwner { owner: parent, .. } => {
                        // For Receiving<_> objects, the address owner is actually an object.
                        // If it was actually an address, we should have caught it as an input and
                        // it would already have been in authenticated_for_mutation
                        ObjectID::from(*parent)
                    }
                    // We mutated a shared object -- we checked if this object was in the
                    // authenticated set at the top of this loop and it wasn't so this is a failure.
                    owner @ Owner::Shared { .. } | owner @ Owner::Party { .. } => {
                        panic!(
                            "Unauthenticated root at {to_authenticate:?} with owner {owner:?}\n\
                             Potentially covering objects in: {authenticated_for_mutation:#?}"
                        );
                    }

                    Owner::Immutable => {
                        assert!(
                            is_epoch_change,
                            "Immutable objects cannot be written, except for \
                             Sui Framework/Move stdlib upgrades at epoch change boundaries"
                        );
                        // Note: this assumes that the only immutable objects an epoch change
                        // tx can update are system packages,
                        // but in principle we could allow others.
                        assert!(
                            is_system_package(to_authenticate),
                            "Only system packages can be upgraded"
                        );
                        continue;
                    }
                }
            };

            // we now assume the object is authenticated and check the parent
            authenticated_for_mutation.insert(to_authenticate.into());
            objects_to_authenticate.push(parent);
```
