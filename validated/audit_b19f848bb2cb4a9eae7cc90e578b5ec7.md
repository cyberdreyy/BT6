[1](#0-0)

### Citations

**File:** stacks-signer/src/signerdb.rs (L1-44)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
// Copyright (C) 2020-2026 Stacks Open Internet Foundation
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

use std::collections::HashMap;
use std::fmt::Display;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use blockstack_lib::chainstate::nakamoto::NakamotoBlock;
use blockstack_lib::chainstate::stacks::{TenureChangeCause, TransactionPayload};
use blockstack_lib::util_lib::db::{
    query_row, query_rows, sqlite_open, table_exists, tx_begin_immediate, u64_to_sql,
    Error as DBError, FromColumn, FromRow,
};
use clarity::types::chainstate::{BurnchainHeaderHash, StacksAddress, StacksPublicKey};
use clarity::types::Address;
use libsigner::v0::messages::{RejectReason, RejectReasonPrefix, StateMachineUpdate};
use libsigner::v0::signer_state::GlobalStateEvaluator;
use libsigner::BlockProposal;
use rusqlite::functions::FunctionFlags;
use rusqlite::{params, Connection, Error as SqliteError, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use stacks_common::codec::{read_next, write_next, Error as CodecError, StacksMessageCodec};
use stacks_common::types::chainstate::ConsensusHash;
use stacks_common::util::get_epoch_time_secs;
use stacks_common::util::hash::Sha512Trunc256Sum;
use stacks_common::util::secp256k1::MessageSignature;
#[cfg(test)]
use stacks_common::util::secp256k1::Secp256k1PrivateKey;
use stacks_common::{debug, define_u8_enum, error, warn};

```
