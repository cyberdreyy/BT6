Now I have confirmed the exact code path. Let me trace through the vulnerability carefully.

**Root cause**: In `multisig2/src/lib.rs`, `delete_member` (lines 356-379) only purges confirmations/requests where `r.member == member` (i.e., requests *authored* by the removed member). It does **not** scan the `confirmations: LookupMap<RequestId, HashSet<String>>` for entries where the removed member had merely *confirmed* (but not authored) some other still-pending request. That stale confirmation string (`member.to_string()`) remains in the `HashSet<String>` for those other requests. Later, `confirm()` (line 304) counts `confirmations.len() as u32 + 1 >= self.num_confirmations` — it counts raw set size, not verified-live-member count — so a stale confirmation from a now-deleted member still counts toward the K-of-N threshold.

This breaks the binding: **confirmations counted == live members who confirmed**. A request can execute with fewer *live* member confirmations than `num_confirmations` requires, because one confirming member was removed after confirming but before the request finished collecting confirmations, yet their vote is never invalidated. [1](#0-0) [2](#0-1) 

### Title
Stale confirmations from removed multisig members are still counted toward the K-of-N threshold, allowing a request to execute below the required live-member threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts entries in the raw `confirmations: HashSet<String>` for a request against `num_confirmations` without verifying that every entry still corresponds to a current member. `delete_member` only removes confirmations/requests that the removed member *authored* (`r.member == member`), but never scans other pending requests' confirmation sets to strip a stale entry left behind when that same account/key had merely confirmed (not authored) those requests. As a result, a member who is later removed from the multisig can still contribute a "ghost" vote that helps push an unrelated pending request over threshold.

### Finding Description
The contract's core invariant is: a request executes only when at least `num_confirmations` *distinct current members* have confirmed it — i.e. `confirmations.len() == count of live members who confirmed`.

`confirm()` enforces this purely via set size:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [3](#0-2) 

`delete_member()` cleans up state for a removed member, but the cleanup is scoped only to requests *authored* by that member:
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
``` [4](#0-3) 

It never iterates `self.confirmations` to remove the removed member's `to_string()` entry from *other* members' pending requests. Since `confirmations` is a `LookupMap<RequestId, HashSet<String>>` keyed by request, not indexed by confirmer, there is no efficient way for `delete_member` to find and purge those entries — the removed member's confirmation silently persists in any request they confirmed but did not author.

Concretely: with `num_confirmations = 3` and members {A, B, C, D}:
1. A creates request R (transfer).
2. B confirms R → `confirmations[R] = {B}` (1/3).
3. Members execute a separate `DeleteMember { member: B }` request (which is a legitimate, un-privileged multisig action reachable by any 3 confirming members, or even fewer if `num_confirmations` was previously lowered) — B is removed from `self.members`, but `confirmations[R]` is untouched because R was authored by A, not B.
4. C confirms R → `confirmations[R] = {B, C}` (2/3, but B is no longer a member).
5. D confirms R → `confirmations.len() + 1 = 3 >= num_confirmations` → R executes.

R executed with only 2 live-member confirmations (C, D) against a 3-of-N policy, because B's stale vote from before removal was still counted equally.

### Impact Explanation
This is exactly the Critical impact category "a multisig request executed below threshold." Funds (`Transfer`), key/permission changes (`AddKey`), contract upgrades (`DeployContract`), and even further membership/threshold changes (`AddMember`/`DeleteMember`/`SetNumConfirmations`) can all be pushed through with fewer than `num_confirmations` *current* signers agreeing, undermining the entire K-of-N custody guarantee the contract advertises. In the worst case this enables asset transfer or contract redeployment authorized by a minority of the currently trusted member set.

### Likelihood Explanation
No privileged actor beyond ordinary multisig members is required — any legitimate member set that occasionally rotates membership (a normal operational event, e.g., replacing a lost key or offboarding a signer) is exposed. The race only requires a confirmation to be cast on a still-pending request before that confirmer is removed, and the request to remain open (not yet at threshold) at removal time — a realistic, easily engineered sequence rather than a contrived edge case. It does not require any cryptographic break, foundation privilege, or victim key compromise; it is a pure state-management gap in `delete_member`.

### Recommendation
When executing `DeleteMember`, iterate all pending requests' `confirmations` sets (or maintain a reverse index from member → confirmed request ids) and strip the removed member's entry from every set, not just requests they authored. Alternatively, validate membership of every entry in `confirmations` against `self.members` at the time `confirm()` evaluates the threshold (filter stale entries out before comparing against `num_confirmations`), so removed members' past votes never count toward execution.

### Proof of Concept
Using the existing test harness in `multisig2/src/lib.rs`:
1. `MultiSigContract::new(vec![A, B, C, D], 3)`.
2. As A: `add_request(transfer_request)` → `request_id`.
3. As B: `confirm(request_id)` (1 confirmation, from B).
4. As members (A, C, D — 3 confirmations, satisfying current threshold): `add_request_and_confirm(DeleteMember { member: B })` — executes, removing B from `self.members`; note `delete_member` does not touch `confirmations[request_id]`.
5. As C: `confirm(request_id)` → set becomes `{B, C}`, size 2.
6. As D: `confirm(request_id)` → `2 + 1 >= 3` → `execute_request` runs the transfer, even though only C and D are live members who confirmed (B's vote is stale). [1](#0-0) [5](#0-4)

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
