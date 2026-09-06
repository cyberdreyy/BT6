[1](#0-0)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L85-89)
```rust
/// Track N most recently processed block identifiers
pub struct RecentlyProcessedBlocks<const N: usize> {
    blocks: Vec<StacksBlockId>,
    write_head: usize,
}
```
