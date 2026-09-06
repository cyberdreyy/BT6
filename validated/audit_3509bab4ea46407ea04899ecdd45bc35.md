[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L19-20)
```rust
#[cfg(any(test, feature = "testing"))]
use std::sync::LazyLock;
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L36-37)
```rust
#[cfg(any(test, feature = "testing"))]
use stacks_common::util::tests::TestFlag;
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L64-67)
```rust
/// Test flag to stall block validation per endpoint with a matching passphrase
#[cfg(any(test, feature = "testing"))]
pub static TEST_VALIDATE_STALL: LazyLock<TestFlag<Vec<Option<String>>>> =
    LazyLock::new(TestFlag::default);
```
