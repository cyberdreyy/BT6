### Title
Stale confirmations from removed multisig members can execute a request below the live confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts the size of the stored `confirmations` set for a request against `num_confirmations` without re-validating that each recorded confirmer is still a current member. `delete_member` only purges confirmations/requests for which the removed member is the *creator* (`r.member == member`), not confirmations the removed member cast on requests created by others. This lets a request execute with fewer live, currently-authorized confirmers than `num_confirmations` requires.

### Finding Description
The custody binding that must hold is: `confirmations.len() (live members only) >= num_confirmations`. Instead, the code enforces `confirmations.len() (including stale, removed members) >= num_confirmations`.

- `confirm()` only checks that the *current caller* is a member (`current_member()` / `assert_valid_request`), then compares the raw cardinality of the previously accumulated `confirmations: HashSet<String>` to the threshold: [1](#0-0) 

- `delete_member()` removes members and cleans up only requests *created by* that member; it does not scan other requests' `confirmations` sets to strip entries where the removed member appears merely as a confirmer: [2](#0-1) 

Because of this, a confirmation cast while a member was valid remains permanently counted toward the threshold even after that member is removed via a `DeleteMember` action on an unrelated request, so long as the confirmed-but-not-yet-executed request itself wasn't created by the removed member.

### Impact Explanation
This breaks the multisig's core authorization invariant (K-of-N live members must approve). An attacker (or a set of members who used to be authorized) can get a `Transfer`, `FunctionCall`, `AddKey`, etc. request executed with confirmations from fewer than `num_confirmations` *currently valid* members, since one or more confirming identities have since been deleted. This falls under the Critical category: "a multisig request executed below threshold," since funds/keys can move out of the account without the intended number of live approvers.

### Likelihood Explanation
This requires ordinary member actions only (no owner/foundation/redeploy/social engineering): any subset of existing legitimate members can create/confirm a request, later have one of the confirming members removed by another legitimate `DeleteMember` request, and then complete the original request's confirmations. No malicious deployment or external actor privilege is needed beyond normal multisig membership actions, so likelihood is realistic in any multisig lifecycle event where membership changes while requests are still pending.

### Recommendation
When a member is removed in `delete_member`, iterate all pending requests' `confirmations` sets and remove the deleted member's entry from every set (not just requests it created), or alternatively re-validate at `confirm()` time that every entry in the stored `confirmations` set still corresponds to a current member before comparing against `num_confirmations` (recomputing the live count each time).

### Proof of Concept
1. Initialize `MultiSigContract::new` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` to create request `R` (e.g., `Transfer{amount}` to some receiver). `confirmations[R] = {A}`.
3. `B` calls `confirm(R)`. `confirmations[R] = {A, B}` (2 < 3, not executed) — see `confirm` logic at [3](#0-2) .
4. Separately, `C` and `D` (plus one more confirmer, e.g. `A`) create and confirm a `DeleteMember{member: B}` request, which executes `delete_member(B)`. This removes `B` from `self.members` and only cleans requests where `r.member == B` (i.e., requests *created* by B) — `R` was created by `A`, so its `confirmations[R] = {A, B}` set is untouched — see [4](#0-3) .
5. `C` (a live member) calls `confirm(R)`. `confirmations.len() + 1 == 3 >= num_confirmations (3)`, so `R` executes via `execute_request`, even though only `A` and `C` are actually still live members who approved it — `B`'s stale confirmation was counted toward the threshold.

This demonstrates a request executed with only 2 genuinely live confirmations against a nominal 3-of-N policy, i.e., a multisig request executed below the intended live-member threshold.

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
