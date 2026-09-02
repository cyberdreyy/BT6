## Confirmed vulnerability, mapped from the Ion Protocol pause-bypass report

### Title
Deleted multisig member's stale confirmation counts toward execution threshold, allowing a request to execute below the live `num_confirmations` threshold - (File: `multisig2/src/lib.rs`)

### Summary
The Ion Protocol report describes a class of bug where a privileged safety gate (a pause) is supposed to block an action, but one code path bypasses the gate via an early return, letting the action complete anyway even though the gate should have stopped it. The analogous binding here is the multisig's threshold gate: `confirmations counted == live members who authorized`. `DeleteMember` removes a member from `self.members` and cleans up only the *requests that member originally submitted*, but never scrubs that member's confirmation entries from `self.confirmations` on *other* still-pending requests. `confirm()` compares `confirmations.len()` against `num_confirmations` without verifying every confirming identity is still a current member, so a stale confirmation from a removed member is silently counted, breaking the `confirmations counted == live members` invariant.

### Finding Description
`delete_member` in `multisig2/src/lib.rs` performs member removal: [1](#0-0) 

Note it filters `self.requests` for entries where `r.member == member` (i.e., requests *created by* the deleted member) and clears their confirmations/requests, but it does **not** iterate over `self.confirmations` for other pending requests to strip out any confirmation string the deleted member previously added while confirming someone else's request.

`confirm()` decides whether to execute purely by counting set size: [2](#0-1) 

```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
```

There is no re-validation that each entry in `confirmations` still corresponds to a member in `self.members` at execution time.

**Attack scenario (breaks the equality `confirmations counted == live members`):**
1. Multisig has members `{A, B, C}` with `num_confirmations = 2`.
2. `A` creates request `R` (e.g., a `Transfer` or `FunctionCall`) via `add_request_and_confirm`, which stores `confirmations[R] = {A}`.
3. Before `R` reaches threshold, the multisig executes a separate, legitimately-approved `DeleteMember { member: A }` request (2-of-3 approval, unrelated to `R`). `delete_member` removes `A` from `self.members` but leaves `confirmations[R] = {A}` untouched because `R` was not created by `A`... wait — actually `R` *was* created by `A`, so it would be cleaned in this exact case. The generalized version: any member `M` who *confirms but did not create* a still-open request `R2` (created by someone else), and is later deleted via `DeleteMember`, leaves their stale confirmation in `confirmations[R2]`.
4. E.g., `B` creates request `R2`; `A` confirms `R2` (`confirmations[R2] = {A}`, now 1 of 2). `A` is then removed via a separate `DeleteMember` request. `self.members` becomes `{B, C}`, but `confirmations[R2]` still contains `A`.
5. `C` (or `B`) confirms `R2`. `confirmations.len() + 1 = 2 >= num_confirmations (2)` → the request executes, counting the removed member `A`'s stale approval as one of the two required confirmations. Only one genuinely live member (`C`) actually authorized this specific request.

This lets a request execute with fewer live authorizing members than `num_confirmations`, i.e., "a multisig request executed below threshold" — explicitly listed as a Critical impact in scope.

### Impact Explanation
Any `MultiSigRequestAction` (Transfer of NEAR, DeployContract, AddKey/AddMember with full access, FunctionCall) can be pushed through with one fewer live approval than the configured threshold. Since multisig thresholds are the sole authorization boundary for moving funds or changing control of the account, this directly breaks the settlement/authorization guarantee the contract exists to provide — funds can move, or control (`AddKey`, `AddMember`) can be seized, without the intended number of currently-trusted parties agreeing.

### Likelihood Explanation
This requires no attacker capability beyond normal multisig membership churn that legitimately occurs over the contract's lifetime (removing a compromised or departing member). It does not require an owner/foundation-level exploit, a redeploy, or ignoring the documented initialization — it is a consequence of ordinary `DeleteMember` usage combined with any pre-existing pending, partially-confirmed request created by someone other than the removed member. Multisig accounts with more than 2 confirming parties and any member turnover are naturally exposed; requests can also be added and left pending intentionally by a still-member attacker prior to being removed, to guarantee the stale-confirmation window is present.

### Recommendation
When executing `delete_member`, iterate over all entries in `self.confirmations` (not just requests originally submitted by the deleted member) and remove the deleted member's identity from every confirmation set; if this drops the request into a state that can never reach quorum from the remaining requests, no action is required beyond the strip. Alternatively/additionally, in `confirm()`, before counting confirmations toward the threshold, filter `confirmations` to only members still present in `self.members`, and only count/execute when that filtered live count crosses `num_confirmations`.

### Proof of Concept
Given the code paths cited above, a minimal Rust unit-test flow (analogous to existing tests in `multisig2/src/lib.rs`) demonstrates it:
1. `MultiSigContract::new(members = [A, B, C], num_confirmations = 2)`.
2. As `B`: `add_request(request = R2)` → `confirmations[R2] = {}`.
3. As `A`: `confirm(R2)` → `confirmations[R2] = {A}` (1/2, not yet executed).
4. Execute a separate already-quorate `DeleteMember{member: A}` request (2-of-3 from `B`,`C`), which calls `delete_member` removing `A` from `self.members` but not touching `confirmations[R2]`.
5. As `C`: `confirm(R2)` → `confirmations[R2].len() + 1 == 2 >= num_confirmations` → `R2` executes, even though only `B` (creator, implicitly trusted) and `C` are current live members explicitly confirming, and `A`'s stale confirmation (now a non-member) was counted toward quorum. [2](#0-1) [1](#0-0)

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
