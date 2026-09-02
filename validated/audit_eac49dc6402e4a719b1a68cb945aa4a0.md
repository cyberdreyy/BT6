### Title
Stale confirmations from removed multisig members are not purged, allowing a request to execute below the live-member confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` removes a member from `self.members` and deletes only the *requests that member itself created*, but it never scans the `confirmations` map to strip that member's confirmation entries from *other* pending requests they had confirmed. Because `confirm()` authorizes execution purely by counting the size of the stored `confirmations` set against `num_confirmations`, a stale confirmation left behind by a removed member still counts toward the threshold, letting a request execute with fewer live, currently-authorized members than the configured quorum requires.

### Finding Description
The binding the multisig is supposed to preserve is:

`number of confirmations from CURRENT members >= num_confirmations`

`confirm()` checks this by comparing the raw size of the `confirmations` `HashSet<String>` for a request against `self.num_confirmations`: [1](#0-0) 

That set is populated with the string form of whichever `MultisigMember` confirmed, with no re-validation at confirm-time (or at delete-time) that every entry still corresponds to a member in `self.members`.

`delete_member` only cleans up requests that the removed member itself *created* (`r.member == member`); it never touches the `confirmations` map for requests created by *other* members: [2](#0-1) 

Contrast this with `remove_request`, which is only invoked when a request is deleted or fully executed — it does not run over all outstanding requests when a member is deleted, so any request that the now-deleted member had previously confirmed (via `confirm()`) keeps that confirmation counted forever, or until the request is separately deleted/executed.

As a result, once a member is removed, their earlier confirmations on still-pending requests remain and are added to the tally used in `confirm()`'s threshold check: [3](#0-2) 

This breaks the equality between "confirmations counted" and "live members who actually authorized the action."

### Impact Explanation
This lets a `MultiSigRequest` (e.g. a `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract`) execute with confirmations from fewer live/current members than `num_confirmations` mandates — i.e. a multisig request executed below threshold, which is explicitly a Critical-severity outcome for this system (funds can be moved, or governance actions like adding a new full-access key can be taken, by a set of members smaller than the configured quorum).

### Likelihood Explanation
No privileged actor or special conditions are required beyond the normal governance flow already supported by the contract: (1) a member confirms a pending request without pushing it to quorum, (2) that member is later removed via a normal `DeleteMember` request (itself reaching quorum from the remaining members, which is an ordinary, expected multisig operation, not a foundation/owner/victim-key action), and (3) the original request is confirmed by the remaining members to reach the nominal quorum count including the stale entry. The bug is triggered purely by two sequential legitimate multisig operations; no key theft, redeploy, or off-chain interception is needed.

### Recommendation
When deleting a member in `delete_member`, iterate over `self.confirmations` (not just `self.requests`) and remove the deleted member's entry from every request's confirmation set, or alternatively re-validate at `confirm()`-execution time that every account/key in the stored confirmation set is still present in `self.members` before treating its count as satisfying `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` where `R` is `Transfer{amount}` to an external receiver → `confirmations[R] = {A}` (size 1).
3. `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (size 2, below threshold, not yet executed).
4. Separately, `A`, `C`, `D` create and confirm a `DeleteMember{member: B}` request (3/4 members, reaches quorum) → executes `delete_member`, removing `B` from `self.members`. Per `multisig2/src/lib.rs:361-371`, only requests *created by* `B` are purged; `R` (created by `A`) is untouched, so `confirmations[R]` still contains `B`.
5. `C` calls `confirm(R)`. In `confirm()` (`multisig2/src/lib.rs:304`), `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)` → `R` executes the transfer, even though only `A` and `C` are current members who actually authorized it — one fewer live confirmation than the configured 3-of-4 threshold requires.

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
