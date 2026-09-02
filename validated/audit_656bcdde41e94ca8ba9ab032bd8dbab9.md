Confirmed. This is a valid analog: the `delete_member` function in `multisig2/src/lib.rs` only purges pending requests/confirmations for requests where the removed member was the *original submitter* (`r.member == member`), but never scans other pending requests' `confirmations: HashSet<String>` to strip a stale confirmation entry left behind by the member being removed. This breaks the "confirmations counted versus live members" binding named in the rules.

### Title
Stale confirmations from removed multisig members still count toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`confirm` counts entries in a `HashSet<String>` of confirmer identities against `self.num_confirmations` without checking that each confirming identity is still a current member at execution time. `delete_member` only cleans up requests that the removed member themselves *created* (`r.member == member`), not confirmations the removed member left on *other* pending requests. As a result, a request can be executed with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
In `confirm`, the threshold check is purely numeric over the stored confirmation strings: [1](#0-0) 
No re-validation is performed that every string in `confirmations` still corresponds to an entry in `self.members`.

`delete_member` removes a departing member from `self.members` and deletes only the pending requests that member originally submitted: [2](#0-1) 
It does not iterate `self.confirmations` values to strip the departing member's identity string from confirmation sets of requests submitted by *other* members. The only invariant enforced is that the total member count stays `>= num_confirmations`: [3](#0-2) 

Concretely, with members `{A, B, C}` and `num_confirmations = 2`:
1. `B` calls `add_request` for a `Transfer`/`FunctionCall`/`AddKey` action (request created by `B`).
2. `A` calls `confirm` on that request — `confirmations = {A}` (only 1 of 2, not yet executed).
3. Through a separate, properly-confirmed multisig request, the group removes `A` via `DeleteMember { member: A }`. `delete_member` only inspects requests where `r.member == A` (i.e., requests `A` itself submitted) — the pending request from step 1, submitted by `B`, is untouched, so its `confirmations` set still contains `A`.
4. `C` (still a live member) calls `confirm` on `B`'s pending request. `confirmations.len() + 1 == 2 >= num_confirmations`, so `execute_request` runs — even though the two confirmers backing execution are `A` (no longer a member) and `C`, i.e., only **one** currently-live member (`C`) plus the request's own creator `B` actually endorse it live, not two independent live confirmations as the `k`-of-`n` design promises.

This crosses the exact threshold-versus-live-membership boundary described in the rules: "confirmations counted versus live members."

### Impact Explanation
This is a Critical-class break per the rubric: "a multisig request executed below threshold." The `MultiSigRequestAction::Transfer`, `FunctionCall`, `AddKey`/`AddMember`, `DeployContract`, etc. actions can move NEAR, grant access keys, or deploy code once the (stale) threshold is met, so funds or privileges can be moved with fewer live approvals than the configured `num_confirmations` requires — the accounting of "confirmations" no longer reflects the set of members who actually still hold that authority at execution time.

### Likelihood Explanation
No special privilege beyond ordinary multisig membership is required — this occurs through normal, sequential use of `add_request`, `confirm`, and a `DeleteMember` action, all reachable by any current member. It does not require the foundation, a redeploy, or a malicious validator. The only requirement is timing: a confirmation exists on a pending request before the confirming member is removed, and that request is not deleted/re-confirmed before the removal executes — a realistic scenario in any long-lived multisig with pending, slow-moving requests (e.g., large transfers awaiting more signers) during a membership rotation.

### Recommendation
When removing a member in `delete_member`, iterate all entries in `self.requests`/`self.confirmations` (not just those submitted by the removed member) and strip the removed member's identity string from every confirmation set; alternatively, validate at `confirm`-time (and before `execute_request`) that every stored confirming identity is still present in `self.members`, discarding stale ones from the count.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2` via `MultiSigContract::new`.
2. As `B`: `add_request(Transfer{amount})` → `request_id = 0`.
3. As `A`: `confirm(0)` → `confirmations(0) = {A}`.
4. Separately, get 2 confirmations to execute `DeleteMember{member: A}` (this only requires the two other live members, e.g. `B` and `C`, since it doesn't touch request 0). `delete_member` removes `A` from `self.members` but does not touch `confirmations(0)`.
5. As `C`: `confirm(0)` → `confirmations(0).len() + 1 == 2 >= num_confirmations(=2)` → `execute_request` runs the `Transfer`, even though `A` is no longer a member and only `C` (plus creator `B`) are live participants. [1](#0-0) [2](#0-1)

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
