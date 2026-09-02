### Title
Multisig `confirm()` counts confirmations from removed members, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member()` in `multisig2/src/lib.rs` only purges *requests originally submitted* by the member being removed. It never purges *confirmations already cast by that member on other members' pending requests*. Since `confirm()` decides whether to execute a request purely by comparing `confirmations.len()` (a `HashSet<String>` of member identities) against `self.num_confirmations`, a confirmation recorded by a member before their removal continues to count toward the threshold after they are removed. This breaks the binding `confirmations counted == confirmations from currently live/authorized members`, letting a request execute with fewer *actually authorized* signers than `num_confirmations` requires.

### Finding Description
`confirm()` looks up the confirmations set for a request and executes as soon as the set size plus the new confirmation reaches `num_confirmations`: [1](#0-0) 

The only membership check performed inside `confirm()`/`assert_valid_request()` validates that the **current caller** is a member; it never re-validates the identities already stored in the `confirmations` set: [2](#0-1) 

`delete_member()` removes the member from `self.members`, deletes their access key, and cleans up only the requests that member *originated* (`r.member == member`). It does **not** scan `self.confirmations` for entries containing the removed member's identity on requests submitted by other members: [3](#0-2) 

Because `MultisigMember` identities are stored as plain strings in a `HashSet<String>` per request (`confirmations: LookupMap<RequestId, HashSet<String>>`, see struct definition), once a member's string is inserted into that set it stays there forever unless the whole request is deleted — regardless of whether that member is later removed from `self.members`. [4](#0-3) 

The identical defect exists in the legacy `multisig/src/lib.rs`: `DeleteKey` only purges requests where `r.signer_pk == pk` (i.e. requests *submitted* by that key), not confirmations cast by that key on other pending requests. [5](#0-4) 

### Impact Explanation
The custody binding a K-of-N multisig is supposed to enforce is: *a request executes only once K distinct, currently-authorized signers have confirmed it*. This bug lets a request execute with fewer than K live signers, because a stale confirmation from a member who has since been removed (e.g. for being compromised, malicious, or simply rotated out) still counts toward `num_confirmations`. This directly maps to the listed Critical impact: "a multisig request executed below threshold." Any action type is affected — `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc. — so this can lead to unauthorized NEAR transfers, unauthorized key/member additions, or unauthorized contract redeployment approved by fewer live parties than the contract's stated security threshold.

### Likelihood Explanation
This requires no external/unprivileged attacker action beyond the normal multisig workflow: a member submits a request, another member confirms it, and later that confirming member is legitimately removed from the multisig (a routine operation, e.g. due to key rotation or compromise response) without the remaining members realizing the earlier request is still pending with a stale confirmation baked in. Since `delete_member()` gives no warning that other pending requests may retain the removed member's confirmation, and there is no monitoring/cleanup step exposed to detect this, the state can arise from ordinary multisig operations. A malicious member who anticipates their own removal could also deliberately pre-confirm a malicious request to bank a "vote" that survives their removal.

### Recommendation
When removing a member in `delete_member()` (and `DeleteKey`/equivalent in `multisig/src/lib.rs`), iterate over all `self.confirmations` entries and strip the removed member's identity from every confirmation set, not just the requests that member originated. Alternatively, validate at `confirm()`-execution time that every identity in the stored confirmation set is still a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with 4 members `A, B, C, D` and `num_confirmations = 3`.
2. `A` calls `add_request(Transfer{...})` → `request_id = 0`, confirmations = `{}`.
3. `B` calls `confirm(0)` → confirmations = `{B}` (1 < 3, not executed).
4. The group later approves and executes a separate request `DeleteMember{B}` (requires 3 confirmations from `A, C, D`) — `delete_member()` runs: `self.members.len()-1 (=3) >= num_confirmations (=3)` passes, `B`'s access key is deleted, `B` is removed from `self.members`. Because request `0` was submitted by `A` (not `B`), it is untouched — confirmations for request `0` remain `{B}`.
5. `C` calls `confirm(0)` → confirmations = `{B, C}` (2 < 3, not executed).
6. `D` calls `confirm(0)` → `confirmations.len()+1 = 3 >= num_confirmations (3)` → request `0` executes the `Transfer`.

Result: the transfer executed with confirmations from `B` (removed, no longer a member), `C`, and `D` — only 2 of the 3 *currently live* members (`C`, `D`) actually authorized it, i.e., the request executed below the intended live-member threshold.

### Citations

**File:** multisig2/src/lib.rs (L126-133)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
}
```

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
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
