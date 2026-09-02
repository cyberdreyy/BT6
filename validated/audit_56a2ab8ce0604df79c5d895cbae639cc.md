### Title
Deleting a compromised multisig member does not invalidate their prior confirmations, allowing requests to execute below the intended `num_confirmations` threshold - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
The binding a K-of-N multisig must maintain is: `confirmations recorded on a pending request == confirmations from currently-authorized members`. In both `multisig/src/lib.rs` and `multisig2/src/lib.rs`, removing a compromised or rotated member (`DeleteKey`/`DeleteMember`) purges only the *requests* that member originated, but never scrubs that member's *confirmations* left on other members' still-pending requests. A stale confirmation from a since-revoked signer therefore still counts toward `num_confirmations`, letting a request execute with fewer live-member confirmations than the threshold requires.

### Finding Description
`confirm()` counts entries in `self.confirmations` (a `HashSet` of signer identities) against `self.num_confirmations`: [1](#0-0) [2](#0-1) 

When a member/key is removed via `DeleteMember`/`DeleteKey`, cleanup only targets requests *created by* that member (`r.member == member` / `r.signer_pk == pk`) and the `num_requests_pk` counter — it never iterates `self.confirmations` to strip that member's prior votes from other outstanding requests: [3](#0-2) [4](#0-3) 

`delete_member` even asserts that the *remaining member count* stays `>= num_confirmations`, giving a false impression that the threshold invariant is preserved — but that check only compares cardinalities of the member set and the configured threshold; it says nothing about which specific confirmations are still valid: [5](#0-4) 

Consequently, for a 2-of-3 multisig (members A, B, C):
1. A (later found compromised) confirms a pending `Transfer` request created by B. Confirmations = {A} (1/2).
2. Members remove A via a `DeleteMember`/`DeleteKey` request, reaching the required 2 confirmations for that governance action. A is removed from `self.members`; A's own requests are purged, but A's confirmation entry on B's pending Transfer request is untouched.
3. C confirms the same Transfer request. `confirmations.len() as u32 + 1 >= num_confirmations` evaluates `1 + 1 >= 2` → true, and the transfer executes — with only one confirmation (C) from a currently-authorized member, plus one stale confirmation from a revoked member (A).

The claimed authorization ("2 confirmations recorded") no longer equals the actual live-member confirmations ("1 live confirmation"), breaking the custody binding that a K-of-N threshold is meant to enforce for `Transfer`, `AddKey`/`AddMember`, `FunctionCall`, etc.

### Impact Explanation
This is a Critical-class issue per the given rubric: "a multisig request executed below threshold." Funds (`Transfer` actions) or privileged operations (`AddKey`, `FunctionCall`) can be pushed through with fewer live-member approvals than configured, undermining the entire security guarantee of the K-of-N scheme. The bug is exploitable purely through the contract's normal, documented flow (create request → confirm → later revoke a signer → confirm again) — no reliance on ignoring initialization or on a redeploy.

### Likelihood Explanation
Requires a pending request to exist with a confirmation from a member that is later removed — a realistic operational sequence (e.g., revoking a compromised or departing member's key while other requests are in flight, given the `REQUEST_COOLDOWN`/request lifetime window). No special privilege beyond normal multisig operation is needed once this ordering occurs; the flaw is in the bookkeeping, not in bypassing an access check.

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, `DeleteKey` handling in `multisig/src/lib.rs`), also scrub that member's identity from `self.confirmations` on every outstanding request (not only requests it created), e.g. iterate `self.requests`/`self.confirmations` and remove the member's entry from each confirmation set, potentially re-checking whether requests remain valid afterward. Alternatively, re-validate at `confirm()`/execution time that all counted confirmers are still current members before allowing execution.

### Proof of Concept
Given `MultiSigContract::new([A, B, C], 2)`:
1. `B` calls `add_request(Transfer{..})` → `request_id = 0`.
2. `A` calls `confirm(0)` → confirmations = {A}, not yet executed (1 < 2).
3. Members submit and confirm a `DeleteMember{member: A}` request (requires 2 confirmations from currently valid members, e.g. B and C) → executes, removing A from `self.members`, but request `0`'s confirmation set still contains `A`.
4. `C` calls `confirm(0)` → `confirmations.len() + 1 = 2 >= num_confirmations (2)` → the Transfer executes, funded by confirmations {A (revoked), C}, i.e. only one live-member approval instead of the required two. [1](#0-0) [3](#0-2)

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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
