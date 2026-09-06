[1](#0-0)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L61-63)
```rust
/// How far below the burnchain tip the signer keeps a record that it sanctioned the reorg of
/// a tenure. A fork deeper than this would cause much bigger problems than a stale conflict.
const MAX_FORK_DEPTH: u64 = 100;
```
