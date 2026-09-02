## Finding: Stale confirmations from deleted multisig members/keys are still counted toward the execution threshold

### Title
Multisig contract executes requests using confirmations from removed members/keys, bypassing the K-of-N threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
The `MultiSigContract` guarantees that a request only executes once `num_confirmations` distinct current members have confirmed it. However, when a member (or access key) is removed via `DeleteMember`/`DeleteKey`, the contract only purges *requests originated by* that member — it never scans the `confirmations` map to strip that member's confirmations from *other, still-pending* requests. A stale confirmation from a deleted member therefore continues to count toward the threshold on any other open request, letting that request execute with fewer live-member confirmations than `num_confirmations` requires.

### Finding Description
`confirm()` decides whether to execute a request purely by comparing the size of the stored confirmation set to `num_confirmations`: [1](#0-0) 

`delete_member()` (multisig2) removes the member from `self.members` and deletes only the *requests the member itself created* (`r.member == member`); it does not touch `self.confirmations` for requests created by others: [2](#0-1) 

The original `multisig` contract has the analogous flaw in the `DeleteKey` action handler inside `execute_request()`, which also only clears requests signed by the deleted key (`r.signer_pk == pk`), leaving that key's confirmations on other pending requests intact: [3](#0-2) 

The binding that should hold is:
```
count(confirmations[request_id] ∩ current_members) >= num_confirmations  ⇒  execute
```
But the actual code checks:
```
len(confirmations[request_id]) >= num_confirmations  ⇒  execute
```
These are not equivalent once a confirming member is later removed — the set `confirmations[request_id]` is never reconciled against `self.members` (multisig2) / the live access-key set (multisig).

### Impact Explanation
This breaks the multisig's core authorization invariant: a request (e.g. `Transfer`, `AddKey`, `FunctionCall`) can be executed with fewer *current* member/key confirmations than `num_confirmations` mandates, because a phantom confirmation from an already-removed member/key is still counted. This is exactly a "multisig request executed below threshold" — funds or privileged actions can be authorized without the intended quorum of live signers, which is a Critical-severity custody/authorization failure.

### Likelihood Explanation
No special privilege beyond being (at some point) a legitimate confirming member is required — this can happen through entirely ordinary, benign-looking operational sequences (a member confirms a pending request, is later removed as part of routine key rotation, and a remaining member then confirms the same still-open request). No malicious deployment, redeploy, or owner action is needed beyond the standard `confirm`/`DeleteMember`/`DeleteKey` flows already exposed by the contract, so the likelihood of this occurring — accidentally or by a member deliberately timing a removal — is high.

### Recommendation
When removing a member/key (`delete_member` in multisig2, the `DeleteKey` action in multisig), iterate all entries in `self.confirmations` (not just requests signed/owned by that member) and remove the deleted member/key's public key or account id from each confirmation set. Alternatively, re-validate at `confirm()` time that every entry in `confirmations[request_id]` still belongs to `self.members` before comparing the count against `num_confirmations`.

### Proof of Concept
Using `multisig2` with 3 members `A, B, C` and `num_confirmations = 2`:
1. `A` calls `add_request(X)` where `X` is a `Transfer` request. `confirmations[X] = {}`.
2. `B` calls `confirm(X)`. `confirmations[X] = {B}` (1 < 2, request stays open per [4](#0-3) ).
3. Separately, `A` and `C` create/confirm a `DeleteMember{member: B}` request, which executes and removes `B` from `self.members` via `delete_member()` — this only deletes requests *created by* `B`, so `confirmations[X] = {B}` is left untouched ( [5](#0-4) ). Members are now `{A, C}`, and `num_confirmations = 2` remains valid since `members.len()-1 (2) >= num_confirmations (2)` before removal.
4. `A` calls `confirm(X)`. `confirmations[X].len() (1) + 1 = 2 >= num_confirmations (2)` → the `Transfer` executes ( [6](#0-5) ), even though only one currently-live member (`A`) actually approved it; `C` never confirmed, and `B`'s confirmation is stale.

This demonstrates a request executing with only 1 of 2 required live-member confirmations, violating the K-of-N guarantee.

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
