### Title
Multisig request confirmations from a removed member remain counted toward the confirmation threshold, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
When a member/key is removed from the multisig via `DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`), the contract only purges requests that were *created* by that member and their `num_requests_pk` counter. It never scans the `confirmations` map to strip that member's prior confirmations from *other* requests created by different members. Those stale confirmations still count toward `num_confirmations` in `confirm()`, letting a request execute with fewer live, currently-authorized confirmers than the configured threshold.

### Finding Description
The intended invariant is: `confirmations.len() == number of distinct, currently-live members who approved`. `confirm()` only checks: [1](#0-0) 

It never re-validates that every public key/account already stored in `confirmations` is still a current member — it only checks that the *caller* confirming right now is a current member (`assert_valid_request` → `current_member()`), not the historical confirmers already in the set.

`delete_member` (multisig2) removes outstanding *requests created by* the removed member and clears its own `num_requests_pk` entry, but does not iterate `self.confirmations` to remove the member's confirmation from requests created by someone else: [2](#0-1) 

The same gap exists in the `multisig` (v1) contract's `DeleteKey` handling, which filters requests by `r.signer_pk == pk` (the creator) only: [3](#0-2) 

So a confirmation recorded by member B on request R (created by member A) survives B's later removal from the multisig. When any remaining live member subsequently confirms R, the threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` counts B's stale confirmation as valid, executing R with fewer genuinely-authorized live confirmers than `num_confirmations` requires.

This is exactly the "confirmations counted versus live members" custody binding: the equality that should hold is
`count(confirmations on R) == count(members in confirmations ∩ current self.members)`,
but after a member removal this becomes `count(confirmations on R) > count(live members who confirmed R)`.

### Impact Explanation
This breaks the K-of-N security guarantee of the multisig: a `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeleteMember` action can be executed with effectively fewer live-member approvals than `num_confirmations`. This is a "multisig request executed below threshold," which is Critical impact per scope — funds can be moved or membership/keys manipulated with less authorization than the contract's own configured policy requires.

### Likelihood Explanation
Any existing member can trigger the vulnerable state through completely ordinary usage: create a request, get a partial confirmation from a member, then have that member removed for any reason (e.g., legitimate key rotation, compromise response) before the request is confirmed/deleted. No privileged bypass, redeploy, or foundation action is needed — only the normal `add_request` / `confirm` / `DeleteMember`/`DeleteKey` flows available to any member. The 15-minute `REQUEST_COOLDOWN` for `delete_request` and the fact that stale requests are never proactively swept make the window realistic.

### Recommendation
When removing a member/key, iterate all outstanding requests and strip the removed member's entry from every request's `confirmations` set (not just requests it created), or alternatively re-validate at `confirm()` time that every already-recorded confirmer in the set is still a current member before counting it toward the threshold.

### Proof of Concept
1. Deploy multisig2 with members `{A, B, C}` and `num_confirmations = 2`.
2. `A.add_request({receiver_id: multisig, actions: [Transfer{amount: X}]})` → `request_id = R`.
3. `B.confirm(R)` → `confirmations[R] = {B}` (1 confirmation, below threshold).
4. Through normal multisig flow, members confirm a separate request `DeleteMember{member: B}` and it executes, removing B from `self.members` — `self.confirmations[R]` is untouched (per `delete_member`, `multisig2/src/lib.rs:355-379`).
5. `A.confirm(R)`: `confirmations[R].len() (1, containing removed B) + 1 (A) = 2 >= num_confirmations (2)` → `execute_request` fires the `Transfer`, executed with only one genuinely live-member confirmation (A) instead of the required two. [4](#0-3)

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
