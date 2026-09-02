### Title
Stale Confirmations from Deleted Multisig Members Allow Request Execution Below Live-Member Threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges the requests that the deleted member itself *created*, and only clears the `confirmations` set for *those* requests. It never scans the `confirmations` sets of other, still-pending requests to strip out entries contributed by the member being removed. As a result, a confirmation cast by a member before their removal remains counted forever, and `confirm` can later reach `num_confirmations` using a mix of stale (removed) and live confirmations, executing a request with fewer *live* members having actually approved it than the configured threshold.

### Finding Description
`confirm` counts confirmations purely by set size:

```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [1](#0-0) 

`delete_member` is the only place that removes stale confirmations, and it does so incorrectly — it filters requests by `r.member == member` (the request *creator*), not by whether `member` appears inside any request's confirmation `HashSet`:

```
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    assert(
        self.members.len() - 1 >= self.num_confirmations as u64,
        "Removing given member will make total number of members below number of confirmations",
    );
    let request_ids: Vec<u32> = self
        .requests
        .iter()
        .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
        .collect();
    for request_id in request_ids {
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    self.num_requests_pk.remove(&member.to_string());
    self.members.remove(&member);
    ...
}
``` [2](#0-1) 

The binding that should hold is:
`confirmations recorded for a pending request ⊆ current live members`

Because `delete_member` never removes a departing member's entry from `confirmations` on requests *created by other members*, this invariant is broken: a request can carry a confirmation from an account that is no longer a `MultisigMember`. `assert_valid_request`, called from both `confirm` and `delete_request`, only checks that the *caller* is currently a member; it never re-validates the *existing* confirmation entries in the set against current membership:

```
fn assert_valid_request(&mut self, request_id: RequestId) {
    assert(self.current_member().is_some(), ...);
    assert(self.requests.get(&request_id).is_some(), ...);
    assert(self.confirmations.get(&request_id).is_some(), ...);
}
``` [3](#0-2) 

Consequently `confirm`'s threshold check (`confirmations.len() + 1 >= num_confirmations`) can be satisfied by counting a removed member's stale confirmation plus fewer live confirmations than `num_confirmations`, executing arbitrary `MultiSigRequestAction`s (including `Transfer`, `FunctionCall`, `DeployContract`, `AddKey`) with less real approval than the multisig's configured security threshold.

### Impact Explanation
This falls under the Critical impact category "a multisig request executed below threshold." Any privileged multisig action (fund transfer, key management, contract redeploy) can be pushed through with fewer genuinely live confirming members than `num_confirmations` mandates, because a departed member's old confirmation on an unrelated pending request continues to count. This is not privileged-user-only: any unprivileged former member (or an attacker who briefly became then was removed as a member, e.g. after a key compromise remediation) leaves a "ghost vote" usable by the remaining members (or in combination with a since-compromised key) to under-collateralize the approval threshold.

### Likelihood Explanation
The scenario requires only ordinary multisig lifecycle operations that are explicitly supported and expected to be used: creating a request, confirming it partially, and later removing a member via `DeleteMember` while other requests remain pending. No exploitation of a bug outside the documented flow, no owner/foundation cooperation beyond the multisig members' own normal `confirm`/`delete_member` actions is needed. Any multisig that rotates members (a normal operational security practice) while it has outstanding un-executed requests is exposed.

### Recommendation
When executing `DeleteMember`, iterate over **all** entries in `self.confirmations` (not just requests created by the removed member) and remove the deleted member's identifier from every confirmation set. Alternatively, revalidate confirmation sets at `confirm`/`execute` time by intersecting recorded confirmations with the current `self.members` set before comparing the count to `num_confirmations`, so removed members can never contribute to reaching the threshold.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. Member `B` calls `add_request` for a sensitive `MultiSigRequest` (e.g., `Transfer` of funds), request id `X`. No confirmation yet.
3. Member `A` calls `confirm(X)` — `confirmations[X] = {A}` (1 of 2 needed) — [1](#0-0) .
4. Members `B` and `C` create and confirm a separate request `Y = DeleteMember { member: A }` reaching the 2-confirmation threshold; `Y` executes, calling `delete_member(promise, A)`. Because the filter in `delete_member` only matches requests where `r.member == A` (i.e., requests A itself authored), request `X` (authored by `B`) is untouched and `confirmations[X]` still contains `A` — [2](#0-1) .
5. `A` is now removed from `self.members`, leaving only `{B, C}` as live members, both of whom have never actually approved request `X`.
6. Member `C` calls `confirm(X)`. `confirmations[X].len() (=1, containing stale A) + 1 >= num_confirmations (2)` is true, so `execute_request(X)` runs — [4](#0-3) .
7. Request `X` (e.g., a fund transfer) executes with only one truly live member (`C`) having approved it, one short of the configured 2-of-N threshold — the removed member `A`'s stale confirmation was silently counted as if it were live.

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
