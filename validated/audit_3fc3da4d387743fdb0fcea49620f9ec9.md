### Title
Stale Confirmations from Removed Multisig Members Allow a Request to Execute Below the Live-Member Confirmation Threshold - (File: `multisig2/src/lib.rs`)

### Summary
In `multisig2`, when a member is removed via `MultiSigRequestAction::DeleteMember`, the contract only purges confirmations for requests that member itself *originally created* — it does not remove that member's confirmation entries from *other* requests where the member had simply added a confirmation. As a result, a confirmation cast by a member who has since been deleted still counts toward the `num_confirmations` threshold, allowing a request to be executed with fewer live, currently-authorized confirmations than the configured K-of-N threshold requires.

### Finding Description
`confirm()` tallies confirmations purely by `HashSet<String>` length compared against `self.num_confirmations`, with no re-validation that every entry in the set still corresponds to a current member: [1](#0-0) 

`delete_member()` is the only place that prunes confirmations tied to a removed member, but it filters strictly on `r.member == member` — i.e. requests that member *created* (`MultiSigRequestWithSigner.member`), not requests where the member is merely present in that request's `confirmations` set: [2](#0-1) 

Because `confirm()` stores confirmers in a separate `confirmations: LookupMap<RequestId, HashSet<String>>` keyed independently from the request's originating `member`, `delete_member` never scrubs a removed member's entry out of `confirmations` for requests created by *other* members: [3](#0-2) 

The intended binding is: `confirmations_from_live_members(request) >= num_confirmations` before a request executes. The actual code enforces `confirmations.len() >= num_confirmations`, where `confirmations` can include stale entries from accounts no longer in `self.members`. This is the exact bug class from the report — a security-critical operation (in this analog: executing an arbitrary multisig `MultiSigRequestAction` including `Transfer`, `FunctionCall`, `DeployContract`, `AddKey`) is authorized off state that doesn't reflect the current, live set of trusted approvers, just as the Diamond upgrade was authorized off a hash of only *some* of the parameters actually executed.

### Impact Explanation
This breaks a threshold/authorization binding explicitly called out as Critical: "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `DeployContract`, or `AddKey` action can be pushed through with confirmations that include a former member's stale approval, meaning the *actual* number of currently-authorized signers who agreed is below `num_confirmations`. Since these multisig contracts commonly custody funds and control account keys/contract code, this can lead to unauthorized fund transfers, unauthorized key additions, or unauthorized code upgrades approved with insufficient live consent.

### Likelihood Explanation
This requires no external attacker key compromise — it only requires normal multisig lifecycle events that are explicitly supported and documented: a member confirms a pending request, then that same member is later removed via a legitimate `DeleteMember` request (member turnover is a normal, expected operation for a long-lived treasury multisig). Any member who is aware of this bug can then engineer or wait for such a sequence to push through a request using the stale confirmation, effectively lowering the real threshold. No special privileges beyond being a current or former member are needed, and the flaw is deterministic and always reachable via the standard API.

### Recommendation
When executing `DeleteMember`, iterate all outstanding requests and remove the deleted member's entry from every request's `confirmations` set (not only requests it originally created), or alternatively, when tallying confirmations in `confirm()`, filter the confirmation set live against `self.members` (i.e., recompute `confirmations.iter().filter(|m| self.members.contains(m)).count()`) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request(R)` for e.g. a `Transfer` action. `confirmations[R] = {}`.
3. `B` calls `confirm(R)` → `1 < 3` → `confirmations[R] = {B}`.
4. Members execute a separate, fully-confirmed request `MultiSigRequestAction::DeleteMember { member: B }`. This calls `delete_member`, which only scrubs requests where `r.member == B` (i.e., requests *B* created) — `R` was created by `A`, so its `confirmations[R] = {B}` is left untouched. `B`'s access key is deleted and `B` is removed from `self.members`; members are now `{A, C, D}`, still `>= num_confirmations (3)`, so the removal succeeds.
5. `C` calls `confirm(R)` → `confirmations[R].len() + 1 = 2 < 3` → `confirmations[R] = {B, C}`.
6. `D` calls `confirm(R)` → `confirmations[R].len() + 1 = 3 >= 3` → `execute_request(R)` fires the `Transfer`.

Result: `R` executed with confirmations `{B, C, D}`, but `B` was no longer a member at execution time — only 2 live members (`C`, `D`) actually authorized it, one below the configured `num_confirmations = 3` threshold. [4](#0-3) [5](#0-4)

### Citations

**File:** multisig2/src/lib.rs (L120-133)
```rust
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
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
