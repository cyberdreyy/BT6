### Title
Stale Confirmations From Removed Multisig Members Still Count Toward the Execution Threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` decides whether a request has reached quorum purely by counting entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set for that request, without verifying that every recorded confirmer is still a current multisig member. `delete_member` only purges requests and confirmations that were *authored* by the removed member; it never scans other pending requests to strip confirmation entries left by a member being deleted. As a result, a confirmation cast by a member before their removal remains valid and countable toward quorum on requests authored by someone else, letting a request execute with fewer than `num_confirmations` distinct *live* members having actually approved it.

### Finding Description
The binding the contract is supposed to maintain is:
```
number_of_valid_confirmations(request) == |{ m ∈ confirmations(request) : m ∈ current_members }|
```
and execution should only proceed when this value reaches `num_confirmations`.

Instead, `confirm()` checks the raw cardinality of the stored set: [1](#0-0) 

`delete_member` removes the departing member from `self.members`, deletes only requests where `r.member == member` (i.e., requests *originated* by that member), and clears `num_requests_pk`, but it never iterates `self.confirmations` to remove that member's string from confirmation sets on requests originated by other members: [2](#0-1) 

Consequently, if member `B` confirms a request authored by member `A`, and `B` is subsequently removed via a separate `DeleteMember` request, `B`'s confirmation on `A`'s request is never invalidated. When enough *other* members later confirm, the stale entry from the now-non-member `B` is still counted, and the request executes even though fewer than `num_confirmations` currently-authorized members actually approved it.

### Impact Explanation
This breaks the core multisig custody invariant: a request (which can be an arbitrary `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc.) can be executed with fewer live, currently-authorized approvals than the configured `num_confirmations` threshold. This falls squarely under the Critical impact category "a multisig request executed below threshold," since funds or privileged actions (adding keys, deploying code) can be authorized without the intended number of current signers agreeing.

### Likelihood Explanation
This does not require any external validator, malicious node, or foundation cooperation — it only requires normal multisig operation: a member confirms a pending request, is later removed as part of ordinary membership rotation (a common, expected operation), and the original request is still pending. No malicious collusion is even strictly necessary; it can occur through ordinary business processes (e.g., offboarding an employee/key while other requests are still outstanding), making this a realistic and moderately likely scenario in any long-lived multisig deployment.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests authored by the removed member) and remove the departing member's identifier from every confirmation set. Alternatively, validate at `confirm()` time (when counting toward quorum) that each entry in the confirmation set still corresponds to `self.members.contains(...)`, discarding stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new([A, B, C, D], 3)`.
2. `A.add_request(transfer_request)` → `request_id = 0`; `confirmations(0) = {}`.
3. `B.confirm(0)` → `confirmations(0) = {B}` (`1 < 3`, not executed).
4. Separately, `A`, `C`, `D` reach quorum (3/4) on a `DeleteMember{B}` request; it executes via `execute_request` → `delete_member`, removing `B` from `self.members`. Because request `0` was authored by `A` (not `B`), the filter in `delete_member` (`r.member == member`) does not touch it, so `confirmations(0)` is untouched and still contains `B`.
5. Now only `{A, C, D}` are members, but `num_confirmations` is still `3`.
6. `C.confirm(0)` → `confirmations(0) = {B, C}`, length `2`, `2+1 >= 3` → `execute_request` fires.
7. The transfer executes with only `C`'s live confirmation plus `B`'s stale one — i.e., with just 2 of the 3 required *current* members' actual consent, violating the multisig threshold guarantee.

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
