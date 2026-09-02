I have enough evidence to confirm the vulnerability in `multisig2/src/lib.rs` (and analogously `multisig/src/lib.rs`): confirmations recorded by a member are not purged from *other* pending requests when that member is removed via `DeleteMember`/`DeleteKey`.

### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
In `MultiSigContract`, when a member is removed via `MultiSigRequestAction::DeleteMember`, the contract only deletes *requests originated by* that member; it never scrubs that member's confirmation entries from the `confirmations` map for *other* still-pending requests. `confirm()` later counts those stale entries toward `num_confirmations`, so a request can execute with fewer live, currently-authorized confirmations than the configured threshold.

### Finding Description
`confirm()` determines execution readiness purely from the size of the `HashSet<String>` stored per `request_id`, without checking that each entry still corresponds to a current member: [1](#0-0) 

`delete_member()` removes the departing member from `self.members` and deletes only the requests *they authored* (`r.member == member`); it does not touch `self.confirmations` entries the member may have already placed on requests authored by someone else: [2](#0-1) 

`current_member()` is only used to validate the *caller* of `add_request`/`confirm`; it is never used to re-validate the *existing* confirmations already stored in the set: [3](#0-2) 

The binding this breaks: `num_confirmations` should equal the count of confirmations from members who are members *at execution time*. Instead it equals `len(confirmations set)`, which can include confirmations from accounts/keys that are no longer members. The K-of-N multisig control (used to gate `Transfer`, `DeployContract`, `AddKey`, `FunctionCall`, etc.) silently degrades to "K-of-(N minus however many confirmers were later removed)".

The identical pattern exists in the legacy `multisig/src/lib.rs`, where `delete_key` removes only requests signed by the deleted key (`r.signer_pk == pk`), never scrubbing that key's confirmations from other pending requests, and `confirm()` counts the raw `HashSet<PublicKey>` length the same way: [4](#0-3) [5](#0-4) 

### Impact Explanation
This crosses the "multisig request executed below threshold" boundary called out as Critical impact. A malicious or compromised member/key that confirms a harmful `Transfer`, `DeployContract` (contract upgrade), or `AddKey` (full-access key) request before being removed leaves a permanent "ghost vote" behind. Once removed, the remaining live members believe they are the only source of authority, but their subsequent confirmations combine with the ghost vote to reach `num_confirmations` sooner than intended — e.g. in a 3-of-4 setup where the compromised member confirmed, only 2 more confirmations from the 3 remaining legitimate members are needed instead of a full re-evaluation with 3 live approvals, effectively lowering the security threshold below what governance configured, and can result in a promise (transfer of NEAR, key addition, or contract deploy) executing with fewer genuine approvals than `num_confirmations` mandates.

### Likelihood Explanation
This requires no privileged access beyond having been a member at some point (e.g., a leaked/rotated access key, a departing team member, or a DAO member removed for cause). Any such former signer can pre-confirm one or more pending requests before removal is finalized, or any current member could intentionally confirm several outstanding requests and then be legitimately voted out — the ghost confirmations persist regardless of intent. Because request execution can be delayed (requests must exist ≥15min before deletion, but there is no time bound forcing immediate execution or expiry tied to membership changes), the window for this to matter is realistic in any operationally active multisig with membership churn.

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate all pending requests' confirmation sets (not just requests authored by the removed member) and remove any entry matching the removed member/key, decrementing effective progress accordingly. Alternatively, validate at `confirm()`-time (and at the moment the threshold check triggers execution) that every entry in the `confirmations` set still corresponds to a `self.members.contains(...)` entry, discarding stale ones from the count.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. Attacker controls (or compromises) member `A`. `B` creates a malicious `Transfer` request to the attacker's account via `add_request`.
3. `A` calls `confirm(request_id)` — `confirmations` set now has 1 entry (`A`), per [6](#0-5) .
4. Governance detects `A`'s key is compromised and submits/confirms a `DeleteMember { member: A }` request, which succeeds and removes `A` from `self.members` — but the confirmation `A` already placed on the Transfer request in step 3 is never scrubbed, per [7](#0-6) .
5. Now only `C` needs to call `confirm(request_id)` once (bringing the stale-inclusive count to 2) is still short of 3 — but with two more legitimate confirmations from `C` and `D` (2 live approvals), the set reaches 3 total (1 stale + 2 live) and `confirm()` executes the `Transfer`, per [8](#0-7) , even though only 2 of the 3 currently-authorized members (`B`, `C`, `D`) actually approved it — one fewer live confirmation than the configured `num_confirmations = 3` threshold.

### Citations

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
        }
    }
```

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
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

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
