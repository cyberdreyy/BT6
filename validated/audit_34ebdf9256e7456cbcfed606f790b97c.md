This repository's `multisig2` contract (an account-based K-of-N multisig, unrelated to the Gnosis Safe/HSG guard in the external report) has an analogous flaw in how it tracks "confirmations counted" versus "live members." When a member is removed, the contract only purges *requests originated by* that member — it never purges that member's lingering confirmation entries on *other* pending requests that member had confirmed. Because `confirm()` only counts set size (`confirmations.len()`) against `num_confirmations` without re-validating that every confirming party is still a current member, a stale confirmation from a since-removed member can be combined with real confirmations from current members to push a request past the confirmation threshold and execute it — even though fewer *live* members actually approved it than the configured threshold.

### Title
Stale confirmations from removed multisig members can be counted toward execution threshold, allowing requests to execute below the live-member confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` removes a departing member from `self.members` and deletes only the *requests that member created*, but does not scrub that member's confirmation entries from the `confirmations` map for other pending requests they had previously confirmed. `confirm()` compares only `confirmations.len()` to `self.num_confirmations`, with no re-validation that every recorded confirmer is still in `self.members`. This lets a stale confirmation from a removed member count toward the threshold, so a request can execute with fewer real, live-member approvals than `num_confirmations` requires. [1](#0-0) [2](#0-1) 

### Finding Description
The intended security invariant of the multisig is: `count(confirmations from members ∈ current self.members) >= self.num_confirmations` before a request executes. The actual check implemented is: `count(confirmations recorded, regardless of current membership) >= self.num_confirmations`.

`confirm()` performs no filtering of stale entries:
```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [3](#0-2) 

`delete_member` only cleans up requests where the removed member was the *originator* (`r.member == member`), leaving that member's confirmation string in the `confirmations: LookupMap<RequestId, HashSet<String>>` for every other request they previously confirmed but did not create:
```rust
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
``` [4](#0-3) 

Since `confirmations` is keyed independently of `self.members`, a confirmation recorded by member `D` on request `R` (created by someone else) survives `D`'s removal from `self.members`. Later, when enough *new* confirmations accumulate on `R`, the stale entry from `D` is still summed into `confirmations.len()`, satisfying the threshold check even though `D` is no longer an authorized signer.

### Impact Explanation
This breaks the core custody binding of the multisig: "number of live-member approvals ≥ configured threshold" before funds move or privileged actions (`Transfer`, `AddKey`, `FunctionCall`, `AddMember`, `DeleteMember`) execute. An attacker (or a coalition below the true threshold) can pre-position a stale confirmation from a member slated for removal, then have that member removed through a legitimate `DeleteMember` request, and subsequently reach the confirmation count using fewer live approvals than `num_confirmations` mandates. This is a "multisig request executed below threshold" scenario — Critical impact, since it can authorize unauthorized transfers of NEAR or arbitrary function calls/key additions on the multisig account without the true configured level of consensus.

### Likelihood Explanation
Requires coordination: the request must be created and partially confirmed before the confirming member is removed, and removal itself still requires reaching the (real, contemporaneous) `num_confirmations` threshold. It's most exploitable by a set of colluding members who intentionally sequence confirm/delete-member operations, or opportunistically when member turnover happens to occur while a request has outstanding confirmations. It does not require exploiting any external system, redeploys, or victim keys — only ordinary use of `confirm`, `add_request`, and `DeleteMember` as documented, so it is a realistic, not merely theoretical, path.

### Recommendation
When executing `DeleteMember`, iterate over *all* pending requests (not just those the removed member originated) and prune the removed member's entry from every `confirmations` `HashSet`. Alternatively (and more robustly), have `confirm()`/`execute_request` re-validate, at execution time, that every entry in the request's `confirmations` set still belongs to `self.members`, only counting live-member confirmations toward `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request` to create transfer request `R` (no auto-confirm).
3. `B` calls `confirm(R)` → `confirmations = {B}`.
4. `D` calls `confirm(R)` → `confirmations = {B, D}` (2 of 3 needed).
5. `A`, `B`, `C` create and confirm a `DeleteMember{ member: D }` request (3 of 4 members, satisfies `members.len()-1 (=3) >= num_confirmations (3)`); it executes, removing `D` from `self.members`. `R`'s `confirmations` set is untouched and still contains `D`.
6. `C` calls `confirm(R)` → `confirmations.len()` becomes 3 (`B, D, C`) which is `>= num_confirmations (3)`, so `R` executes the transfer — despite only `B` and `C` being genuine current members who approved it (2 of 3 live members), because the stale confirmation from removed member `D` was counted.

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
