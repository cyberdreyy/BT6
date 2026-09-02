### Title
Stale confirmations from removed multisig members allow request execution below the configured threshold - (`multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member()` only purges pending requests that were *created* by the removed member; it does not purge that member's *confirmations* recorded on requests created by other members. Because `confirm()` counts entries in the `confirmations` `HashSet<String>` regardless of whether those entries still correspond to current `members`, a request can be executed once `confirmations.len() + 1 >= num_confirmations` even though one or more of the counted confirmers are no longer live multisig members. This breaks the custody binding that the number of counted confirmations must equal the number of confirmations from currently-live, authorized members.

### Finding Description
`confirm()` computes whether a request is ready to execute purely from the size of the stored `confirmations` set for that `request_id`: [1](#0-0) 

`delete_member()` removes the member from `self.members` and cleans up only the requests where `r.member == member` (i.e., requests the removed member *originally created*), then deletes that member's `num_requests_pk` entry: [2](#0-1) 

It does **not** scan `self.confirmations` to strip the removed member's identifier from confirmation sets on requests created by *other* members. `remove_request()` (used only when a request is fully confirmed/executed or explicitly deleted) also never audits confirmation entries against current membership: [3](#0-2) 

Consequence: if member `M` confirms request `R` (created by a different member) and is later removed from the multisig (via a separate, properly-confirmed `DeleteMember` request), `R`'s confirmation set still contains `M`'s identifier. Any subsequent live member calling `confirm(R)` will have their vote counted together with `M`'s stale vote, potentially reaching `num_confirmations` with fewer *live* approving members than the threshold requires.

Concrete trace (num_confirmations = 3, members = {A, B, C, D}):
1. `A.add_request(R)` — Transfer action, `confirmations(R) = {}`.
2. `B.confirm(R)` → `confirmations(R) = {B}` (`0+1 < 3`).
3. `C.confirm(R)` → `confirmations(R) = {B, C}` (`1+1 < 3`).
4. Separately, members A, B, D fully confirm a `DeleteMember{C}` request; `delete_member` removes `C` from `self.members` but does not touch `confirmations(R)`, which still equals `{B, C}`.
5. `D.confirm(R)`: `confirmations(R).len() (=2) + 1 >= 3` → executes `R`.

At execution time, live members are {A, B, D}; the transfer was authorized by only `B` and `D` as live members (plus the stale `C` entry), i.e. 2 genuine live-member approvals against a configured threshold of 3.

### Impact Explanation
This is a multisig request (e.g., `Transfer`, `AddKey`, `FunctionCall`, further `DeleteMember`/`AddMember`) executed with fewer live-member confirmations than the contract's configured `num_confirmations` threshold — matching the Critical impact category "a multisig request executed below threshold." Funds held by the multisig account can be transferred, or access keys/members can be added/removed, without the intended quorum of currently-trusted parties, defeating the core K-of-N custody guarantee of the contract.

### Likelihood Explanation
The scenario requires only ordinary, otherwise-legitimate multisig operations: a request partially confirmed by a member who is subsequently removed through the normal `DeleteMember` governance flow (a routine operational event — e.g., off-boarding a departing signer or key rotation). No malicious code injection, redeploy, or out-of-scope privilege is needed beyond the multisig's own documented `confirm`/`DeleteMember` flow; the flaw is in the accounting invariant between `confirmations` and `members`, not in any single actor's misuse of special privilege.

### Recommendation
When removing a member in `delete_member`, iterate over all pending `requests`/`confirmations` and remove the deleted member's identifier from every confirmation set (not just requests it created), re-checking whether any request should be dropped below a re-validated confirmation count. Alternatively, when counting confirmations in `confirm()`, intersect the stored confirmation set with `self.members` (i.e., only count confirmations from entries still present in `self.members`) before comparing against `num_confirmations`.

### Proof of Concept
Extend the existing test harness in `multisig2/src/lib.rs` tests module:
1. `let mut c = MultiSigContract::new(members(), 3);` with members {A, B, C(key), D(key)}.
2. `A` (as current_member) calls `add_request(transfer_request)` → `request_id`.
3. `B` calls `confirm(request_id)` (confirmations len 1).
4. `C`'s key calls `confirm(request_id)` (confirmations len 2).
5. Separately: create+confirm (with A, B, D) a `DeleteMember{C}` request so it executes, removing `C` from `self.members`.
6. Assert `c.confirmations.get(&request_id).unwrap()` still contains `C`'s serialized identity (stale entry) — confirms it wasn't purged.
7. `D` calls `confirm(request_id)` → observe the transfer request executes (`c.requests.len() == 0`), even though only `B` and `D` are live members who ever approved it, i.e., 2 live approvals versus the configured `num_confirmations = 3`. [1](#0-0) [2](#0-1)

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

**File:** multisig2/src/lib.rs (L381-404)
```rust
    /// Removes request, removes confirmations and reduces num_requests_pk - used in delete, delete_key, and confirm
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .unwrap_or_else(|| env::panic_str("Failed to remove existing element"));
        // decrement num_requests for original request signer
        let original_member = request_with_signer.member;
        let mut num_requests = self
            .num_requests_pk
            .get(&original_member.to_string())
            .unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_member.to_string(), &num_requests);
        // return request
        request_with_signer.request
    }
```
