## Title
Removed multisig member's stale confirmation still counts toward `num_confirmations`, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` (and the key-based `DeleteKey` equivalent in the legacy `multisig` contract) only purges pending *requests originally created by* the removed member/key. It never scans the `confirmations` map for entries where the removed member had *confirmed someone else's* request. As a result, a stale confirmation from a member who has since been deleted from the multisig continues to count toward `num_confirmations`, letting a request execute with fewer live-member confirmations than the configured threshold requires.

### Finding Description
`confirm()` records the confirming identity as a string key in `self.confirmations` for the target `request_id` and executes the request once `confirmations.len() + 1 >= self.num_confirmations`: [1](#0-0) 

When a member is removed via `MultiSigRequestAction::DeleteMember`, `delete_member` only removes pending requests **authored by** that member (`r.member == member`) and clears `num_requests_pk`; it never inspects the `confirmations` map for other requests where the deleted member is a **confirmer**: [2](#0-1) 

The same gap exists in the legacy `multisig` contract's `DeleteKey` handling, which filters outstanding requests by `signer_pk` (the request author) but similarly never strips that key from `confirmations` entries of requests it did not author: [3](#0-2) 

This breaks the custody-relevant equality that should hold for a K-of-N multisig: `confirmations_counted(request) == confirmations_by_live_members(request)`. Once a member/key is removed, any confirmation it previously placed on a *different, still-pending* request remains counted, so that request can reach `num_confirmations` and execute (including `Transfer`, `AddKey`, `FunctionCall`, etc.) with strictly fewer currently-authorized confirmers than the threshold mandates.

### Impact Explanation
This is Critical: a multisig request (including a NEAR `Transfer` moving funds out of the account) can be executed with fewer live confirmations than the configured `num_confirmations`, i.e. "a multisig request executed below threshold." The intended security guarantee — that K of the *current* N members must approve any action — is silently violated whenever membership changes while requests are outstanding, which is a normal and expected operational event (e.g., revoking a departing employee or a compromised key).

### Likelihood Explanation
High. No special privilege is needed beyond being (or having been) a legitimate member at some point — a routine operational sequence triggers it: (1) a member confirms a pending request without pushing it over threshold, (2) that member is later removed from the multisig for any normal reason, (3) the remaining members confirm as usual. There is no requirement that the removal itself be malicious; the bug fires from ordinary confirm-then-remove-member ordering, and the multisig owners have no visibility into the fact that a stale confirmation is silently counted (`get_confirmations` will even list the now-removed member as a confirmer).

### Recommendation
When executing `DeleteMember` (or `DeleteKey`), iterate over all entries in `confirmations` (not just `requests` authored by that member) and remove the deleted member's confirmation from every pending request's confirmation set. Additionally, `confirm()` should validate that all recorded confirmers for a request are still current members before counting them toward `num_confirmations`, rather than trusting confirmations recorded at time-of-confirm indefinitely.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. Member `A` calls `add_request` with a `Transfer` action sending the account's full balance to `attacker`.
3. Member `D` calls `confirm(request_id)` → `confirmations = {D}` (1 of 3, request not yet executed).
4. Separately, the team decides to revoke `D` (e.g., departing employee) and executes a `DeleteMember{member: D}` request through the normal multisig flow. `delete_member` removes `D` from `members` and deletes any requests **created** by `D`, but the Transfer request from step 2 was created by `A`, so its `confirmations` entry `{D}` is left untouched.
5. Members `B` and `C` each call `confirm(request_id)`. `confirmations.len()` becomes 3 (`D`, `B`, `C`) which is `>= num_confirmations (3)`, so `execute_request` fires and the `Transfer` is sent — even though only 2 of the current 3 live members (`A`, `B`, `C`) ever confirmed it, one short of the required 3-of-3 live threshold. [2](#0-1) [1](#0-0)

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
