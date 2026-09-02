### Title
Confirmations from deleted multisig members remain counted toward the execution threshold, allowing requests to execute below the live-member quorum - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` executes a request once `confirmations.len() + 1 >= num_confirmations`, but `delete_member` only purges requests *created* by the removed member; it never purges that member's *confirmations* recorded on requests created by other members. A confirmation cast by a member who is later removed remains counted toward quorum for any pre-existing request they confirmed, letting that request execute with fewer live, authorized confirmations than `num_confirmations` requires.

### Finding Description
`confirmations: LookupMap<RequestId, HashSet<String>>` stores confirmation strings per request, independent of the `members` set [1](#0-0) . When a member confirms a request that they did not create, their confirmation is inserted into that request's `HashSet` [2](#0-1) .

`delete_member` is the only path that scrubs confirmation state, and it filters strictly by requests whose *creator* (`r.member`) equals the member being deleted: [3](#0-2) 
It does not scan `confirmations` for entries where the deleted member appears as a *confirmer* on someone else's request. Those stale confirmation strings remain in the `HashSet` for those other requests indefinitely.

Later, when quorum is evaluated in `confirm`, the check `confirmations.len() as u32 + 1 >= self.num_confirmations` counts every string in the set, including ones belonging to now-deleted members [4](#0-3) . There is no re-validation that every entry in `confirmations` still corresponds to a member present in `self.members` at execution time.

The binding that should hold is:
```
count(confirmations for request R) == count(live members who approved R)
```
After a member is removed, this equality breaks: `confirmations.len()` stays the same, but the number of *live* approving members drops by one (or more, if multiple confirmers are later removed). If enough approvals were gathered before removals occurred, the request can cross the `num_confirmations` threshold and execute even though fewer than `num_confirmations` currently-authorized members actually approve it.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." A transfer, `FunctionCall`, `DeployContract`, or `AddKey`/`AddMember` action can be executed with real confirming authority below the configured `k`-of-`n` threshold, because stale confirmations from removed members still count. This can allow moving NEAR out of the multisig account, deploying new contract code, or adding an access key with fewer than `num_confirmations` currently-valid approvals — undermining the entire security guarantee the multisig contract is designed to provide.

### Likelihood Explanation
The scenario only requires the ordinary multisig workflow: (1) a request is created and partially confirmed by some members, (2) one of those confirming members is subsequently removed via a `DeleteMember` request (a normal, expected operation, e.g. key rotation or offboarding), and (3) the remaining live members confirm the still-pending request, causing it to cross the (unadjusted) threshold and execute. No compromise, redeploy, or externally malicious actor is required — it is a latent state-consistency bug reachable purely through the documented `add_request`/`confirm`/`DeleteMember` API sequence.

### Recommendation
When a member is deleted, iterate over all `requests`/`confirmations` (not just those created by the deleted member) and remove the deleted member's entry from every confirmation `HashSet`. Alternatively, when evaluating quorum in `confirm`, filter `confirmations` to only strings corresponding to current `self.members` before comparing against `num_confirmations`, so removed members' stale approvals never count toward execution.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. Member `A` calls `add_request` for a `Transfer` (request `R`, receiver `X`).
3. Member `B` calls `confirm(R)` → `confirmations[R] = {B}` (1/3).
4. Member `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2/3, not yet executed).
5. Members confirm and execute a separate `DeleteMember { member: C }` request (self-request on the multisig account) — this only removes requests *created by* `C`; it does not touch `confirmations[R]`, which still contains `C`'s entry. Now live members are `{A, B, D}`.
6. Member `D` calls `confirm(R)` → `confirmations.len() == 2` before insert, so `2 + 1 >= 3` is true; `execute_request` runs the `Transfer`.
7. Result: request `R` executed with confirmations from `B`, `D`, and the now-removed `C` — i.e., only 2 currently-live authorized members (`B`, `D`) actually approved, yet the configured 3-of-4 threshold was reported as satisfied.

### Citations

**File:** multisig2/src/lib.rs (L125-133)
```rust
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

**File:** multisig2/src/lib.rs (L299-314)
```rust
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
```

**File:** multisig2/src/lib.rs (L361-374)
```rust
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
