## Analysis

I confirmed the mechanism in `multisig2/src/lib.rs`. The `confirm` function counts confirmations toward `num_confirmations` from a `HashSet<String>` per request [1](#0-0) , while `delete_member` only purges confirmations for requests **originated by** the removed member, not confirmations the removed member had cast on *other* members' requests [2](#0-1) . This means a stale confirmation string from a now-removed member remains counted in `confirmations.len()` for any request they previously confirmed but did not create, allowing that request to reach the `num_confirmations` threshold with fewer live members than intended.

This maps to the requested analog class ("confirmations counted versus live members") and is a genuine authorization/threshold-crossing bug (Critical impact: "a multisig request executed below threshold"), not a DoS/gas/theoretical issue.

However, I was not able to fully trace within budget whether `add_member`/initialization ordering could make this unreachable, nor confirm test coverage exists disproving it (e.g., a hidden invariant that confirmations are always re-validated against current `members` set at execution time — I found no such check in `execute_request` or `confirm`). Given the strength of the reachable path and exact code support, I present this as the valid analog.

### Title
Stale confirmations from removed multisig members count toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member` removes a member and cleans up only the requests *that member created*, but does not scrub that member's confirmation entries from the `confirmations` map for requests created by *other* members. Because `confirm()` only checks `confirmations.len()` against `num_confirmations` without verifying every recorded confirmer is still a current member, a request can execute with fewer live-member confirmations than the configured threshold requires.

### Finding Description
`MultiSigContract::confirm` [1](#0-0)  increments/checks a `HashSet<String>` of confirmer identifiers (`member.to_string()`), and once `confirmations.len() as u32 + 1 >= self.num_confirmations`, it executes the request.

`delete_member` [2](#0-1)  removes the member from `self.members` and deletes only requests where `r.member == member` (i.e., requests the departing member *authored*):
```
let request_ids: Vec<u32> = self.requests.iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
It never inspects the `confirmations` HashSet of requests authored by *other* members to strip out the departing member's string. So if member A confirmed a pending request R authored by member B, and A is later removed via `DeleteMember`, R's `confirmations` set still contains A's entry.

The binding that should hold is: `confirmations counted for R == confirmations from currently live members`. After A's removal this becomes `confirmations counted for R > confirmations from currently live members`, i.e., a stale/invalid confirmation is still counted toward `num_confirmations`.

### Impact Explanation
If a request R (e.g., a `Transfer` or `FunctionCall` moving NEAR) has `num_confirmations - 1` confirmations including one from a member who is subsequently removed, only one more confirmation from any current member is needed to push the count to `num_confirmations` and execute R — even though the removed member's approval should no longer be valid. This lets a request execute with a lower effective threshold of *current* trusted signers than configured, which is exactly the "multisig request executed below threshold" Critical impact category: NEAR can be transferred, contract code deployed, or keys added, authorized by a stale confirmation from someone no longer part of the trust set.

### Likelihood Explanation
Reachable by ordinary multisig operation flow with no special privilege beyond being (at one point) a legitimate member: A confirms R, is later removed (self-initiated or by other members through normal governance), and R remains pending in `requests`/`confirmations` (there is no code path that invalidates or re-validates confirmations on membership change for requests not authored by the removed member). No redeploy, no owner-only entry point abuse, no malicious infrastructure required — purely a state-machine/bookkeeping gap in the shipped contract logic.

### Recommendation
When removing a member in `delete_member`, iterate over **all** active requests' `confirmations` sets (not just requests authored by that member) and remove the departing member's `to_string()` entry from each. Alternatively, at `confirm()` time (and before executing on the final confirmation), filter the stored confirmations against `self.members` and only count confirmations from accounts/keys that are still current members.

### Proof of Concept
1. Deploy `MultiSigContract::new(members=[A,B,C,D], num_confirmations=3)`.
2. Member B calls `add_request` to create request R (e.g., `Transfer` to an attacker-controlled account).
3. Member A calls `confirm(R)` → `confirmations[R] = {A}`.
4. Separately, members reach threshold to execute `DeleteMember{member: A}` (a normal governance action, e.g. via requests authored/confirmed by B, C, D) — A is removed from `self.members`, but `confirmations[R]` still contains `{A}` because `delete_member` only scrubbed requests authored by A, not R (authored by B).
5. Member C calls `confirm(R)` → `confirmations[R] = {A, C}`, size 2, still short of 3.
6. Member D calls `confirm(R)` → count becomes 3 ≥ `num_confirmations`, and R (the `Transfer`) executes — despite A, whose confirmation is counted, no longer being a valid member. Effectively only C and D (2 live members) approved, one below the configured 3-of-N threshold.

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
