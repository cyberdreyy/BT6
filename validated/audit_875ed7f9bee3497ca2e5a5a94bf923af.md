## Finding: Stale confirmations from removed multisig members still count toward the approval threshold

### Title
Multisig request can execute below the live-member confirmation threshold because deleting a member does not purge their confirmations from *other* pending requests - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
This is a valid analog of the reported bug class. The Uniswap report is about a permission (ERC20 approval) that is granted for one purpose but is not revoked/reset when the entity should no longer hold it, letting stale approval balloon and be reused later. The NEAR analog is the multisig contracts' `confirmations` map: when a member is removed via `DeleteMember`/`DeleteKey`, the contract only clears confirmations for requests *created by* that member, but leaves that member's confirmation entries intact on requests created by *other* members. Those stale entries continue to count toward `num_confirmations`, so a request can later execute having effectively fewer live, trusted approvers than the configured threshold.

### Finding Description
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the `confirmations` `HashSet` for that `request_id` against `self.num_confirmations`: [1](#0-0) 

Removal of a member is handled by `delete_member` (multisig2) / the `DeleteKey` branch (multisig), which only cleans up requests that the removed member *authored* (`r.member == member` / `r.signer_pk == pk`), and only removes `num_requests_pk` for that member: [2](#0-1) [3](#0-2) 

Neither routine scans the `confirmations` map to strip the removed member's identifier (`member.to_string()` / `signer_pk`) from confirmation sets belonging to requests authored by *other* members. Consequently, if the removed member had already confirmed such a request before being deleted, that confirmation entry persists in storage and is still counted by `confirm()`'s threshold check.

The binding that should hold is:
```
approvals_counted(request) == approvals_from_current_live_members(request)
```
After a member deletion, this becomes:
```
approvals_counted(request) = approvals_from_live_members(request) + stale_approvals_from_removed_members(request)
```
which is strictly ≥ the correct value, breaking the k-of-n live-member guarantee the multisig is supposed to provide.

### Impact Explanation
This falls under the Critical impact category "a multisig request executed below threshold." A request intended to require `k` live, trusted signers can execute with only `k-1` (or fewer) currently-trusted signers, because one slot in the threshold is silently filled by a member who was deemed untrusted enough to be removed (e.g., a departing team member, a compromised key, or a member removed specifically because of misbehavior). This directly weakens the security assumption the multisig custody model depends on for approving `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc.

### Likelihood Explanation
This requires no attacker code injection — it is triggered by completely ordinary operational sequences that any long-lived multisig will encounter:
1. Member confirms a pending request (but it doesn't yet reach threshold).
2. That same member is later removed via a `DeleteMember`/`DeleteKey` request (a routine, expected multisig operation, e.g. offboarding a signer).
3. The original request, still pending, gets confirmed by other live members and reaches (falsely) the threshold count, executing with the stale confirmation counted.

Given that member turnover on any long-running multisig is expected, and that removal is the standard remediation for a compromised or departing signer, the likelihood of this occurring is not low.

### Recommendation
When a member is deleted, iterate all pending `requests`/`confirmations` entries and remove the deleted member's identifier from every confirmation set (not just requests they authored), decrementing counts consistently. Alternatively, re-validate at execution time that every entry in a request's confirmation set still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Multisig deployed with members `{A, B, C}`, `num_confirmations = 2`.
2. `A` calls `add_request` for `Transfer{amount}` to some receiver → `request_id = R` (author = A).
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (1 < 2, not yet executed): [4](#0-3) 
4. Separately, members agree to remove `B` (e.g. because `B`'s key is suspected compromised) via `DeleteMember{member: B}` on a different request; `delete_member` runs and only removes requests *authored* by `B`; `R` (authored by `A`) is untouched, so `confirmations[R]` still contains `B`: [5](#0-4) 
5. `B` is now removed from `self.members` and can no longer confirm anything new; conceptually B's stake in the multisig should be zero.
6. `C` (a genuinely live member) calls `confirm(R)`. The check `confirmations.len() as u32 + 1 >= self.num_confirmations` evaluates `1 (stale B) + 1 (C) = 2 >= 2` → true, and `execute_request` runs the transfer.
7. Result: `R` executed with the "confirmation" of a removed/untrusted member (`B`) plus only one currently live member (`C`), instead of two live members as configured — the threshold guarantee is broken. [1](#0-0) [2](#0-1)

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

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
