[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-vm/types/src/loaded_data/struct_name_indexing.rs (L62-67)
```rust
    /// Flushes the cached struct names and indices.
    pub fn flush(&self) {
        let mut index_map = self.0.write();
        index_map.backward_map.clear();
        index_map.forward_map.clear();
    }
```

**File:** third_party/move/move-vm/types/src/loaded_data/struct_name_indexing.rs (L103-116)
```rust
    fn idx_to_struct_name_helper<'a>(
        index_map: &'a parking_lot::RwLockReadGuard<IndexMap<StructIdentifier>>,
        idx: StructNameIndex,
    ) -> PartialVMResult<&'a Arc<StructIdentifier>> {
        index_map.backward_map.get(idx.0 as usize).ok_or_else(|| {
            let msg = format!(
                "Index out of bounds when accessing struct name reference \
                     at index {}, backward map length: {}",
                idx.0,
                index_map.backward_map.len()
            );
            panic_error!(msg)
        })
    }
```
