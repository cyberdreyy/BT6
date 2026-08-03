[1](#0-0)

### Citations

**File:** third_party/move/move-vm/types/src/value_traversal.rs (L17-22)
```rust
pub fn find_identifiers_in_value(
    value: &Value,
    identifiers: &mut HashSet<u64>,
) -> PartialVMResult<()> {
    find_identifiers_in_value_impl(value, identifiers)
}
```
