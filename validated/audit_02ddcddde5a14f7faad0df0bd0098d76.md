## Title
Stale Confirmations From Removed Members Allow Multisig Requests To Execute Below the Configured Threshold - (`multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges pending requests that were *authored* by the removed member; it never scans the `confirmations` map for entries where that member merely *confirmed* someone else's still-pending request [1](#0-0) . Because `confirm()` counts quorum purely from the size of the stored `confirmations` set without re-validating that every recorded confirmer is still a current member [2](#0-1) , a removed member's stale confirmation continues to count toward `num_confirmations`, letting a request execute with fewer genuinely-authorized, currently-live confirmations than the configured threshold.

### Finding Description
The invariant the multisig is supposed to enforce is: *a request only executes once `num_confirmations` distinct, currently-authorized members have approved it* — i.e. `confirmations recorded == confirmations from live members`.

`confirm()` implements this purely by cardinality:
```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [3](#0-2) 

`confirmations` is a `HashSet<String>` keyed by `member.to_string()`, populated whenever a still-valid member calls `confirm` [4](#0-3) . Nothing ever removes an entry from that set when the member who produced it is later deleted from the multisig, unless the *request itself* was authored by that member:

```rust
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
    let request_ids: Vec<u32> = self
        .requests
        .iter()
        .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
        .collect();
    for request_id in request_ids {
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    ...
    self.members.remove(&member);
``` [5](#0-4) 

This filter only matches `r.member == member`, i.e. requests the removed member *created*. Any pending request created by a different member on which the removed member merely *confirmed* is left untouched, and its `confirmations` set still contains the now-removed member's string entry. The next `confirm()` call on that stale request counts that ghost entry toward `self.num_confirmations`.

### Impact Explanation
This breaks the "confirmations counted versus live members" custody binding: the contract executes a `MultiSigRequest` (which can include `Transfer`, `FunctionCall`, `AddKey`, `DeleteMember`, etc.) even though fewer than `num_confirmations` *currently authorized* members actually approved it. This is exactly the "multisig request executed below threshold" scenario, categorized Critical, since it undermines the entire security purpose of a k-of-n multisig (protecting against any single compromised/removed signer).

### Likelihood Explanation
This requires no exotic privilege beyond normal multisig operation: it is triggered by the ordinary, expected operational sequence of removing a member (e.g., because their key is suspected compromised or they are offboarded) while a request they had previously confirmed is still pending. No malicious intent by any single party is required — the flaw exists in the accounting logic itself, so it will surface any time membership changes occur in a multisig with outstanding unconfirmed requests, a routine operational pattern.

### Recommendation
When deleting a member, iterate over all entries in `self.confirmations` (not just requests authored by that member) and remove the deleted member's confirmation string from every pending request's confirmation set. Alternatively, have `confirm()` validate, at count time, that every string in the stored `confirmations` set corresponds to a current member of `self.members`, discarding stale entries before comparing against `self.num_confirmations`.

### Proof of Concept
1. Multisig has members `{A, B, C, D}` with `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` for some request `R` (e.g. a `Transfer`) → `requests[R].member = A`, `confirmations[R] = {A}`.
3. `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (2 < 3, still pending) [6](#0-5) .
4. Through a normal, legitimate governance action, members `A, C, D` execute a separate request to `DeleteMember { member: B }` (e.g. suspected key compromise). `delete_member` succeeds; it only removes requests authored by `B` (none exist), so `R`'s `confirmations` set `{A, B}` is left intact [7](#0-6) . Membership is now `{A, C, D}`, still with `num_confirmations = 3`.
5. `C` calls `confirm(R)`. The check computes `confirmations.len() (2, containing stale "B") + 1 = 3 >= num_confirmations (3)`, so `R` executes [3](#0-2) , even though only `A` and `C` are genuinely live, current approvers — one short of the required unanimous 3-of-3 for the current 3-member set.

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L355-374)
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
```
