### Title
Stale confirmations survive `DeleteMember`/re-add, allowing multisig requests to execute below live-member threshold - (`multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges the `requests` and `confirmations` entries for requests **originated** by the removed member, and resets that member's `num_requests_pk` counter, but it never scans other outstanding requests' `confirmations` sets to strip that member's vote. A member who confirmed some other request `X` (that they did not originate) before being removed leaves a phantom entry in `confirmations[X]`. If the identifier is later reused — e.g. the same principal is re-added as a member (`AddMember`) — that phantom vote is still counted toward `num_confirmations` in `confirm`, letting `X` execute with fewer *live, currently-confirming* members than the configured threshold.

### Finding Description
The invariant the code is supposed to enforce is:
`confirmations.len() at execution == number of currently-live members who explicitly approved this specific request`, and that quantity must be `>= self.num_confirmations` **at the time of execution**.

In `confirm`:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [1](#0-0) 
this simply trusts the stored `HashSet<String>` size, without re-validating that every string in it still corresponds to a currently confirming, live member's genuine vote for the current membership epoch.

In `delete_member`:
```
let request_ids: Vec<u32> = self.requests.iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
self.num_requests_pk.remove(&member.to_string());
self.members.remove(&member);
``` [2](#0-1) 
this only deletes requests where `r.member == member`, i.e. requests that member itself *originated* via `add_request`. It does nothing to scrub that member's string from the `confirmations` set of any *other* still-outstanding request they had merely confirmed. `num_requests_pk` is cleared (reset to effectively 0 on next lookup), but the separate `confirmations` LookupMap entries for other requests are untouched.

Exploit / bug-trigger flow:
1. Member `M` (any current member, including a principal that will later be re-added) confirms request `X` that was originated by a different member — `X` now sits with `confirmations[X] = {M}` short of threshold.
2. Members execute a `DeleteMember { member: M }` request (a normal multisig operation) — `delete_member` removes `M` from `members` and resets `num_requests_pk[M]`, but `confirmations[X]` still contains `M`'s string.
3. Members later execute an `AddMember { member: M' }` where `M'` derives the *same* `to_string()` identity as the old `M` (same `AccountId`, or same reused `PublicKey` — both attacker-influenceable if the re-add specifies the public key/account chosen by whoever proposes the request).
4. `current_member()` now recognizes `M'` as live again, but the stale vote in `confirmations[X]` was never removed and never required re-confirmation.
5. Any subsequent live member's `confirm(X)` call now counts `M`'s phantom vote plus the new confirmer(s), reaching `num_confirmations` with fewer *actually live, currently re-affirming* members than the configured threshold, and `execute_request` (e.g. a `Transfer`, `AddKey`, or another `AddMember`) fires.

None of the existing guards catch this: `assert_valid_request` only checks that the **caller** is a current member and that the request/confirmations rows exist [3](#0-2) ; it never re-validates that every already-stored string in `confirmations[request_id]` still maps to a live member. `current_member()` [4](#0-3)  is only invoked for the calling account, not for auditing stored confirmation sets.

### Impact Explanation
A `MultiSigRequest` (fund `Transfer`, `AddKey`, `AddMember`, `DeployContract`, etc.) can execute with a confirmation count that includes at least one phantom vote from a member who is no longer live at execution time, i.e. "a multisig request executed below `num_confirmations` live members" — the explicit Critical category. This can directly move NEAR out of the multisig account, install an unauthorized full-access key, or add an unauthorized member, entirely outside the trust assumptions of the multisig's stated threshold. The blast radius is any multisig instance that experiences member churn (add/remove/re-add) while requests are outstanding — a normal, expected lifecycle event for these contracts, not an exotic scenario.

### Likelihood Explanation
Triggering the phantom-vote condition only requires ordinary membership churn: a member confirms a request, is later removed, and the same identity (same `AccountId` or reused `PublicKey`) is added back — all through the contract's own supported `AddMember`/`DeleteMember` actions with no additional preconditions or cost beyond the requests that any set of members would already be submitting for their own protocol reasons. No special balances or unusual account states are needed, and the bug is deterministic and repeatable on every multisig instance that undergoes this member lifecycle pattern.

### Recommendation
When removing a member in `delete_member`, iterate all outstanding requests and strip the removed member's identity string from every `confirmations` entry (not only requests they originated), or invalidate/require re-confirmation of any request whose confirmation set contains a since-removed member before allowing `confirm` to count it toward threshold. Alternatively, on `confirm`, filter `confirmations.get(&request_id)` down to only strings that currently match a live member (`self.members.contains(...)`) before comparing the count against `num_confirmations`.

### Proof of Concept
```rust
// cargo test in multisig2
#[test]
fn test_stale_confirmation_survives_removal_and_readd() {
    // 1. Init multisig with members [alice, bob, key_c], num_confirmations = 3
    // 2. alice: add_request(transfer X) -> request_id
    // 3. bob: confirm(request_id)         // confirmations[request_id] = {bob}
    // 4. alice: add_request_and_confirm(DeleteMember{ member: key_c })
    //    (assume threshold reached with alice+bob+key_c confirming the delete)
    // 5. alice: add_request_and_confirm(AddMember{ member: key_c })  // same public key re-added
    // 6. key_c: confirm(request_id)
    //    ASSERT: confirmations[request_id].len() before this call == 1 (only bob)
    //    ASSERT: after key_c's confirm, count reaches num_confirmations (3) execution fires
    //    even though only bob + key_c(re-added) ever explicitly confirmed post re-add —
    //    i.e. verify execution happened with < 3 *live-at-execution-time* explicit confirmations
    //    by checking c.get_confirmations / c.requests before vs after and that the phantom
    //    "alice" (or whichever member) vote was never actually re-cast after re-add.
}
```
This test should assert that `confirmations[request_id]` retained a stale entry across the `DeleteMember`/`AddMember` cycle (proving the write is not scrubbed in `delete_member`), and that `confirm` executed the request while the number of members who genuinely reconfirmed after the last membership change was below `num_confirmations`.

### Citations

**File:** multisig2/src/lib.rs (L304-309)
```rust
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
```

**File:** multisig2/src/lib.rs (L322-339)
```rust
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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

**File:** multisig2/src/lib.rs (L407-423)
```rust
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
