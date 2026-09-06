[1](#0-0) [2](#0-1)

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L1-1)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
```

**File:** stacks-signer/src/v0/signer.rs (L22-55)
```rust
use blockstack_lib::chainstate::nakamoto::{NakamotoBlock, NakamotoBlockHeader};
use blockstack_lib::net::api::postblock_proposal::{
    BlockValidateOk, BlockValidateReject, BlockValidateResponse, ValidateRejectCode,
    TOO_MANY_REQUESTS_STATUS,
};
use blockstack_lib::util_lib::db::Error as DBError;
use clarity::codec::read_next;
use clarity::types::chainstate::{StacksBlockId, StacksPrivateKey};
use clarity::types::{PrivateKey, StacksEpochId};
use clarity::util::hash::{MerkleHashFunc, Sha512Trunc256Sum};
use clarity::util::secp256k1::Secp256k1PublicKey;
#[cfg(any(test, feature = "testing"))]
use clarity::util::sleep_ms;
#[cfg(any(test, feature = "testing"))]
use clarity::util::tests::TestFlag;
use libsigner::v0::messages::{
    BlockAccepted, BlockRejection, BlockResponse, MessageSlotID, MockProposal, MockSignature,
    RejectReason, RejectReasonPrefix, SignerMessage, StateMachineUpdate,
};
use libsigner::v0::signer_state::GlobalStateEvaluator;
use libsigner::{BlockProposal, SignerEvent, SignerSession};
use stacks_common::types::chainstate::{StacksAddress, StacksPublicKey};
use stacks_common::util::get_epoch_time_secs;
use stacks_common::util::secp256k1::MessageSignature;
use stacks_common::{debug, error, info, warn};

use super::signer_state::LocalStateMachine;
use crate::chainstate::v1::{SortitionMinerStatus, SortitionsView};
use crate::chainstate::v2::GlobalStateView;
use crate::chainstate::{ProposalEvalConfig, SortitionData, SortitionStateVersion};
use crate::client::{ClientError, SignerSlotID, StackerDB, StacksClient};
use crate::config::{SignerConfig, SignerConfigMode};
use crate::runloop::SignerResult;
use crate::signerdb::{BlockInfo, BlockState, PendingBlockResponses, SignedConflictInfo, SignerDb};
```
