### Title
Stale confirmations from deleted multisig members remain counted toward the confirmation threshold, allowing a request to execute with fewer live-member approvals than `num_confirmations` requires - (File: `multisig2/src/lib.rs`, also affects `multisig/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` (and the analogous `DeleteKey` handling in the older `multisig` contract) only purges *requests originated by* the removed member; it does not purge that member's *confirmations recorded on requests originated by other members*. `confirm()` blindly counts `confirmations.len()` against `num_confirmations` without verifying that every recorded confirmer is still a current member. As a result, a request can reach the confirmation threshold and execute even though one (or more) of the counted confirmations came from an account that is no longer a multisig member at execution time.

### Finding Description
`confirm()` treats the size of the `confirmations` set as ground truth for "how many live members approved this request": [1](#0-0) 

But membership can change between the time a confirmation is recorded and the time the threshold is reached. `delete_member` is the only cleanup path invoked when a member is removed, and it only removes/cleans requests **that the removed member itself created**: [2](#0-1) 

Note the filter `r.member == member` on line 365 - it matches on the *request creator*, not on entries inside `self.confirmations`. Nothing in `delete_member` (or anywhere else) scans `self.confirmations` for other requests that the removed member had confirmed and strips those stale confirmations out. `assert_valid_request`, called from both `confirm` and `delete_request`, likewise only checks that the *caller* is a current member - it never re-validates the *stored* confirmations: [3](#0-2) 

The same class of bug exists in the older key-based contract: `DeleteKey` only removes requests signed by the removed key (`r.signer_pk == pk`), not confirmations that key contributed to other members' requests: [4](#0-3) 

This is the same root-cause pattern as the referenced Malt finding: a piece of state that grants/withdraws authority (`validRoles` / multisig membership) is checked at the time an action is *recorded*, but a *different, unguarded* code path (`hasRole` bypass / stale `confirmations` set) is trusted later without re-verifying that the authority is still valid, breaking the intended binding `confirmations counted == live members who approved`.

### Impact Explanation
This breaks the core multisig custody guarantee that a `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` action requires `K` *currently authorized* approvals. Once a member is removed (e.g., because their key was compromised, or as routine membership rotation), any confirmation they previously placed on a still-pending request created by someone else silently continues to count. This lets a request - including a NEAR `Transfer` moving funds out of the multisig account, or `DeployContract`/`AddKey` giving full access - execute with fewer genuinely live approvals than `num_confirmations`, i.e., a multisig request executed below threshold. This matches the Critical impact category for this asset class (funds moved by fewer authorized parties than the documented threshold).

### Likelihood Explanation
No privileged action beyond ordinary multisig operation is required: any member can call `confirm()` on a pending request created by another member (a normal, expected operation), and membership changes (`DeleteMember`/`DeleteKey`) are a normal governance action already supported by the contract. The vulnerable window (a confirmation recorded, then the confirmer removed, then the request later reaches threshold via other confirmers) is a realistic sequence in any multisig with routine key/member rotation, especially since removals are often prompted by suspected compromise - precisely the case where the compromised member's prior confirmations should be the most suspect, not still trusted.

### Recommendation
When removing a member (`delete_member`/`DeleteKey`), iterate all pending requests and strip the removed member's identifier from every stored `confirmations` set (not just requests the member created), or alternatively re-validate on each `confirm()`/execution that every entry in `confirmations` still corresponds to a current member (filtering out stale entries before comparing against `num_confirmations`).

### Proof of Concept
For `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`:
1. `A` calls `add_request` to create `R1` (e.g., `Transfer` to an attacker-controlled/receiver account). `confirmations[R1] = {}`.
2. `D` calls `confirm(R1)`. `confirmations[R1] = {D}` (1/3, not yet executed) - see `confirm` at [1](#0-0) .
3. Separately, `A`, `B`, `C` create and confirm a `DeleteMember{D}` request (e.g. because `D`'s key is suspected compromised), which executes via `delete_member` - see [2](#0-1) . Because `R1` was created by `A`, not `D`, the filter on line 365 does not touch `R1`; `confirmations[R1] = {D}` is left untouched even though `D` is now removed from `self.members`.
4. `A` calls `confirm(R1)` → `confirmations[R1] = {D, A}` (2/3).
5. `B` calls `confirm(R1)` → `confirmations[R1] = {D, A, B}` = 3, meets `num_confirmations`, and `execute_request` runs the `Transfer`.
6. Result: the request executed with confirmations from only 2 currently-live members (`A`, `B`) plus one stale confirmation from a removed member (`D`), even though the contract's documented guarantee is that `K=3` live members must approve.

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
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
