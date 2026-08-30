[1](#0-0)

### Citations

**File:** core/primitives/src/utils.rs (L1-18)
```rust
#[cfg(feature = "clock")]
use crate::block::BlockHeader;
use crate::hash::{CryptoHash, hash};
use crate::sharding::ChunkHash;
use crate::types::{ChunkExecutionResultHash, ShardId};
use crate::universal_state_init::UniversalStateInit;
use chrono;
use chrono::DateTime;
use near_crypto::{ED25519PublicKey, Secp256K1PublicKey};
use near_primitives_core::account::id::{AccountId, AccountType};
use near_primitives_core::deterministic_account_id::DeterministicAccountStateInit;
use near_primitives_core::types::BlockHeight;
use near_primitives_core::universal_account_id::encode_universal_account_id;
use serde;
use std::convert::AsRef;
use std::fmt;
use std::mem::size_of;
use std::ops::Deref;
```
