[1](#0-0) [2](#0-1) [2](#0-1) [3](#0-2)

### Citations

**File:** multisig/src/lib.rs (L79-89)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize)]
pub struct MultiSigContract {
    num_confirmations: u32,
    request_nonce: RequestId,
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>,
    num_requests_pk: UnorderedMap<PublicKey, u32>,
    // per key
    active_requests_limit: u32,
}
```

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```

**File:** multisig2/src/lib.rs (L1-1)
```rust
use std::collections::HashSet;
```
