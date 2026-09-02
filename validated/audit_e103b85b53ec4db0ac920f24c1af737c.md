### Title
Stale confirmations from removed multisig members still count toward `num_confirmations` threshold, allowing request execution with fewer live-member approvals than required - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` decides whether a request has reached quorum purely by comparing the size of the stored `confirmations` `HashSet` against `self.num_confirmations` [1](#0-0) . When a member is removed via `delete_member`, the code only purges *requests originally created by* that member and their per-member request counters; it does not scan and strip that member's entry from the `confirmations` set of *other* pending requests they had previously confirmed [2](#0-1) . As a result, a confirmation cast by an account that is later removed from `members` remains in the count used to satisfy `num_confirmations`, breaking the intended binding "confirmations counted == confirmations by currently live members".

### Finding Description
- `confirm` reads `self.confirmations.get(&request_id)`, checks the caller hasn't already confirmed, and if `confirmations.len() as u32 + 1 >= self.num_confirmations` it removes and executes the request [3](#0-2) .
- `delete_member` only asserts `members.len() - 1 >= num_confirmations`, filters `self.requests` for entries whose *original signer* (`r.member`) equals the deleted member, and removes confirmations/requests for those specific requests only [2](#0-1) . It never iterates over `self.confirmations` to drop the deleted member's entries from requests created by *other* members.
- Because `confirmations: LookupMap<RequestId, HashSet<String>>` is keyed only by request id and stores raw string identifiers of confirmers [4](#0-3) , once an account/key is removed from `members`, its stale membership string can remain permanently inside another request's `confirmations` set, still counted by `confirm`'s threshold comparison.

This breaks the equality that should hold: `count(live members who confirmed request X) == confirmations.len()`. Instead `confirmations.len()` can include confirmers who are no longer members.

### Impact Explanation
This is a Critical-class issue per the custody binding "confirmations counted versus live members": a multisig request (including a `Transfer` action moving NEAR out of the multisig account, or a `DeleteMember`/`AddKey` action) can execute after receiving fewer *currently valid* confirmations than `num_confirmations` requires, because a stale confirmation from a since-removed member is still tallied. This allows execution of a transfer (or other privileged action) below the configured approval threshold — i.e., NEAR moved, or the account otherwise controlled, by a set of members that no longer satisfies the K-of-N policy the account owner configured.

### Likelihood Explanation
The precondition — a member confirming a request, then being removed from the multisig before quorum is reached on that same request, followed by another confirmation to cross the threshold — is a normal operational sequence for any multisig doing member rotation while multiple requests are pending; it requires no privileged access beyond being (or having been) a legitimate multisig member, and no special timing beyond ordinary transaction ordering. Given multisig accounts are expected to periodically add/remove members (this is a documented supported action, `DeleteMember`), the scenario is readily reachable during normal operation.

### Recommendation
When a member is deleted in `delete_member`, iterate over all pending `requests` (not just those originally signed by the removed member) and remove the deleted member's entry from each `confirmations` set, re-inserting the pruned set (or leaving the request pending with fewer confirmations) rather than allowing stale confirmations to persist. Alternatively, validate at `confirm`-time that every account/key recorded in a request's `confirmations` set is still a current member before counting it toward the threshold.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `X` (e.g., `Transfer` to an external account) — `confirmations(X) = {}`.
3. `B` calls `confirm(X)` → `confirmations(X) = {B}` (1 < 3, not executed).
4. `C` calls `confirm(X)` → `confirmations(X) = {B, C}` (2 < 3, not executed).
5. Separately, members execute a fully-confirmed `DeleteMember { member: C }` request; `delete_member` removes `C` from `self.members` but does not touch `confirmations(X)`, which still contains `"C"` [2](#0-1) . `members` is now `{A, B, D}`, still `>= num_confirmations(3)`, so the delete succeeds.
6. `A` calls `confirm(X)` → `confirmations(X).len() as u32 + 1 = 3 >= num_confirmations(3)` [5](#0-4) , so `execute_request` runs the `Transfer`, even though only two currently-live members (`A` and `B`) ever approved it — one confirmation slot was filled by `C`, who was removed before execution.

This demonstrates NEAR (or any privileged multisig action) being executed with fewer live-member confirmations than the configured threshold, an unauthorized move relative to the account's declared K-of-N policy.

### Citations

**File:** multisig2/src/lib.rs (L126-130)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
```

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
