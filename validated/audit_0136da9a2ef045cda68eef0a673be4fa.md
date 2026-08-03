[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/natives/src/storage_slot.rs (L36-36)
```rust
fn native_borrow_storage_slot_resource(
```

**File:** aptos-move/framework/natives/src/storage_slot.rs (L90-106)
```rust
fn native_borrow_storage_slot_resource_mut(
    context: &mut SafeNativeContext,
    ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    safely_assert_eq!(ty_args.len(), 2);
    safely_assert_eq!(args.len(), 1);

    context.charge(STORAGE_SLOT_BORROW_MUT_BASE)?;

    // Get the address from StorageSlot.addr field
    let storage_slot_ref = safely_pop_arg!(args, StructRef);
    let addr = storage_slot_ref
        .borrow_field(0)?
        .value_as::<Reference>()?
        .read_ref()?
        .value_as::<AccountAddress>()?;
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/storage_slot_or_inline.move (L1-44)
```text
module aptos_framework::storage_slot_or_inline {
    use std::mem;
    use aptos_framework::storage_slot::{Self, StorageSlot};

    /// StorageSlotOrInline found in inconsistent (transient) state, should never happen.
    const ESTORAGE_SLOT_INCORRECTLY_IN_TRANSIENT_STATE: u64 = 1;

    enum StorageSlotOrInline<T> has store {
        Inline{ value: T },
        StorageSlot { slot: StorageSlot<T> },
        Transient,
    }

    public fun new_inline<T: store>(value: T): StorageSlotOrInline<T> {
        StorageSlotOrInline::Inline { value }
    }

    public fun new_storage_slot<T: store>(value: T): StorageSlotOrInline<T> {
        StorageSlotOrInline::StorageSlot { slot: storage_slot::new(value) }
    }

    public fun borrow<T: store>(self: &StorageSlotOrInline<T>): &T {
        match (self) {
            StorageSlotOrInline::Inline { value } => value,
            StorageSlotOrInline::StorageSlot { slot } => slot.borrow(),
            StorageSlotOrInline::Transient => abort ESTORAGE_SLOT_INCORRECTLY_IN_TRANSIENT_STATE,
        }
    }

    public fun borrow_mut<T: store>(self: &mut StorageSlotOrInline<T>): &mut T {
        match (self) {
            StorageSlotOrInline::Inline { value } => value,
            StorageSlotOrInline::StorageSlot { slot } => slot.borrow_mut(),
            StorageSlotOrInline::Transient => abort ESTORAGE_SLOT_INCORRECTLY_IN_TRANSIENT_STATE,
        }
    }

    public fun destroy<T: store>(self: StorageSlotOrInline<T>): T {
        match (self) {
            StorageSlotOrInline::Inline { value } => value,
            StorageSlotOrInline::StorageSlot { slot } => slot.destroy(),
            StorageSlotOrInline::Transient => abort ESTORAGE_SLOT_INCORRECTLY_IN_TRANSIENT_STATE,
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/big_ordered_map.move (L1-1)
```text
/// This module provides an implementation for an big ordered map.
```
