I found a concrete, in-scope analog vulnerability: the `confirmations` set for a pending request in `multisig2` is not purged when a confirming member is later removed from the multisig, so a stale confirmation from a now-deleted member still counts toward the live threshold, breaking the binding `confirmations counted == confirmations from currently-live members`.

### Title
Stale confirmations from removed members count toward execution threshold, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`confirm()` in `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the `confirmations` `HashSet<String>` stored for that `request_id` against `self.num_confirmations` [1](#0-0) . `delete_member()` only purges requests and confirmation sets for requests *created* by the removed member; it does not scan other pending requests to strip out confirmations the removed member had already cast on requests created by someone else [2](#0-1) . As a result, a confirmation from an account/key that is no longer a member of the multisig remains valid and can push a later request over the `num_confirmations` threshold.

### Finding Description
The intended invariant is: a request executes only once at least `num_confirmations` **currently live** members have confirmed it. The code instead enforces: a request executes once the stored `confirmations` set (populated historically, potentially containing removed members) reaches size `num_confirmations`.

Walkthrough:
1. Member A creates request R1 (a `Transfer`, `FunctionCall`, etc. — anything other than the actions filtered by `delete_member`).
2. Member B confirms R1. `confirmations(R1) = {B}`, still below `num_confirmations` (say 3 of 4 members), so R1 stays pending [3](#0-2) .
3. Separately, member B is removed via a `DeleteMember` request executed by other members. `delete_member()` only removes requests whose `request_with_signer.member == B` (i.e., requests *B originated*) — R1 was created by A, so its confirmation set is left untouched [4](#0-3) .
4. Members A and C now confirm R1. `confirmations(R1)` becomes `{B, A, C}` = 3, meeting `num_confirmations = 3`, and the request executes [5](#0-4) .
5. The request executed with only 2 *live* members (A, C) actually confirming — B's stale, no-longer-valid confirmation was counted as if B were still an authorized member.

This directly parallels the external report's bug class: a value used for a critical authorization check (`debtCeiling` computed one way in `borrow()` vs. another way in `debtCeiling()`) diverges from the value that should be authoritative. Here, "confirmations recorded" diverges from "confirmations from currently live members," and the divergent (looser) value is exactly what gates execution of privileged multisig actions (transfers, key/member changes, contract deployment, arbitrary function calls).

### Impact Explanation
This breaks the core authorization guarantee of the multisig: `MultiSigRequestAction::Transfer`, `AddKey`/`AddMember`/`DeleteMember`, `DeployContract`, and arbitrary `FunctionCall` can all be executed by fewer live, currently-trusted members than `num_confirmations` requires. Since the multisig custodies NEAR balance and controls account keys/membership, this allows an unauthorized transfer of funds or unauthorized privileged account changes (e.g., adding a full access key) with confirmations below the configured threshold — i.e., "a multisig request executed below threshold," matching the Critical impact bucket.

### Likelihood Explanation
Any workflow where a member is removed (e.g., off-boarding, key rotation, compromise response) while they have outstanding confirmations on requests they didn't originate leaves those stale confirmations live indefinitely (`confirmations` is a `LookupMap` keyed by `request_id` with no expiry tied to membership). No admin error or malicious intent is required beyond the normal, documented `DeleteMember` flow; the remaining members do not need to realize a stale confirmation exists, and the bug is purely a consequence of `delete_member`'s incomplete cleanup [4](#0-3) .

### Recommendation
On `delete_member`, iterate all pending requests' confirmation sets and remove the deleted member's identifier from each (not just requests they originated), or re-validate at `confirm()` time / execution time that every entry in the stored `confirmations` set still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. As A: `add_request(R1)` (e.g., `Transfer`).
3. As B: `confirm(R1)` → `confirmations(R1) = {B}` (1 < 3, pending).
4. As majority (A, C, D confirm a separate `DeleteMember { member: B }` request) → B is removed from `members`; R1 (created by A, not B) is untouched by `delete_member`'s cleanup loop, so `confirmations(R1)` still contains `"B"`.
5. As A: `confirm(R1)` → `confirmations(R1) = {B, A}` (2 < 3, still pending).
6. As C: `confirm(R1)` → size becomes 3 ≥ `num_confirmations`, `execute_request` runs and the `Transfer` executes — despite only A and C (2 live members) actually confirming; B's stale confirmation counted toward the threshold. [1](#0-0) [6](#0-5)

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
