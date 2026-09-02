### Title
Stale confirmations from removed multisig members/keys let a request execute below the intended confirmation threshold - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
When a multisig member (or access key in `multisig`) is removed via `DeleteMember` / `DeleteKey`, the contract only purges pending **requests that member created**, but never scrubs that member's **confirmations on other still-pending requests**. Those stale confirmations remain in the `confirmations` set and continue to count toward `num_confirmations`, so a request can later execute with fewer live-member approvals than the configured threshold requires — a direct "confirmations counted versus live members" divergence, resulting in a multisig request executed below threshold.

### Finding Description
`confirm()` decides whether to execute a request purely by comparing the size of the stored `confirmations` set to `num_confirmations`: [1](#0-0) 

There is no re-validation that every entry inside `confirmations` still corresponds to a current member at the time of execution.

The removal path (`delete_member`, and equivalently `DeleteKey` in `multisig`) only cleans up requests whose **creator** (`r.member`/`r.signer_pk`) is the member being removed: [2](#0-1) [3](#0-2) 

It never iterates the `confirmations` map to strip the removed member's entry from requests they merely *confirmed* (but did not create). Consequently, a confirmation cast by a member before that member is removed remains valid "forever" and is still tallied toward the threshold on subsequent `confirm()` calls, even though that member is no longer part of `members` (or no longer holds the access key).

**Concrete scenario (multisig2, `num_confirmations = 3`, members = {A, B, C, D}):**
1. Member `A` calls `add_request_and_confirm(R1)` → `confirmations[R1] = {A}`.
2. Member `B` calls `confirm(R1)` → `confirmations[R1] = {A, B}` (still < 3, so it is only stored, not executed).
3. Through a separate, fully-confirmed request, the multisig removes `B` via `DeleteMember`. `delete_member` only deletes requests **created by** `B`; since `B` did not create `R1`, `confirmations[R1]` is left untouched at `{A, B}`, and `B` is removed from `members`.
4. Member `C` (a legitimate, live member) calls `confirm(R1)`: `confirmations.len() + 1 == 3 >= num_confirmations`, so `R1` executes.

`R1` executes with only two *currently live* approvals (`A`, `C`) plus one stale approval from a member (`B`) who has since been removed and can no longer act on behalf of the multisig — i.e., the request executed below the intended live-member threshold. The identical logic and identical gap exist in `multisig`'s `DeleteKey` handling.

### Impact Explanation
This breaks the core custody guarantee of a k-of-n multisig: "a request executes only if it has been approved by k *currently authorized* signers." Because a stale confirmation from a de-authorized member/key still counts, an attacker (or even benign) member set can arrange for arbitrary `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` actions to execute with effectively fewer live approvals than configured — this maps directly to the Critical impact category "a multisig request executed below threshold," which can move NEAR out of the account or grant unauthorized keys/members without the required quorum of currently trusted signers.

### Likelihood Explanation
No privileged foundation/owner access, no redeploy, and no external RPC/reorg dependency is required — only ordinary multisig operation: creating a request, having it partially confirmed, later removing one of the confirming members through the contract's own supported `DeleteMember`/`DeleteKey` action, and then having the remaining members confirm normally. Member/key rotation is an expected, routine multisig lifecycle event, making the precondition easy to hit even unintentionally (e.g., rotating out a compromised/departing signer while a request is mid-flight), not merely a contrived attack path.

### Recommendation
When removing a member/key (`delete_member` in `multisig2`, `DeleteKey` handling in `multisig`), iterate over `confirmations` (not just `requests`) and strip the removed member's/key's entry from every pending request's confirmation set, or equivalently, at `confirm()` time re-validate that every historical confirmer is still `self.members.contains(&member)` (or a currently valid `signer_pk`) before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request_and_confirm(R1)` → confirmations = `{A}`.
3. `B.confirm(R1)` → confirmations = `{A, B}` (not yet executed, 2 < 3).
4. Separately get a fully-confirmed request executed to `DeleteMember { member: B }` (removes `B` from `members`, deletes only requests created by `B`; `R1` untouched since `A` created it).
5. `C.confirm(R1)` → `confirmations.len() + 1 == 3 >= 3` → `R1` executes, even though only `A` and `C` are still-live approvers (2 of 3 required live signers), with `B`'s stale confirmation making up the difference.

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
