## Title
Removed multisig member's confirmation remains counted toward execution threshold, allowing requests to execute below the true quorum of live members - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
When a multisig member (an access key or account) is removed via `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), the contract only purges *requests that member created* — it does not scrub that member's confirmations from *other* pending requests that member had merely confirmed. Because `confirm()` counts confirmations purely by set size and never re-validates that each confirming key/account is still a current member, a stale confirmation from a removed member still counts toward the `num_confirmations` threshold, letting a request execute with fewer live, currently-authorized approvals than required.

### Finding Description
`execute_request`'s threshold check in `confirm()` only compares the size of the stored confirmation set to `num_confirmations`; it never re-checks membership of the accounts/keys inside that set: [1](#0-0) 

`delete_member` (multisig2) and the `DeleteKey` action handler (multisig, v1) only remove pending requests *authored* by the removed member/key — filtering on `r.member == member` / `r.signer_pk == pk` — and never touch the `confirmations` map for requests authored by *other* members where the removed party had already confirmed: [2](#0-1) [3](#0-2) 

As a result, the binding "confirmations counted == confirmations from currently live members" is broken: `confirmations.len()` can include principals that are no longer members, so `confirmations.len() + 1 >= num_confirmations` can become true while the number of *still-authorized* approvers is strictly less than `num_confirmations`.

This is the structural analog of the reported bug: a piece of authorization state (a `Permit2` nonce there, a confirmation-set entry here) is tracked with a scope that is too broad/stale relative to the entity it should be bound to (token+spender there; still-a-member here), so the contract's validation reads state that no longer reflects the entity's real current status.

### Impact Explanation
This allows a `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, `DeployContract`, or any other multisig action to execute despite lacking a genuine quorum of currently-authorized approvers — a request can be executed below the configured threshold. Per the impact rubric this is Critical: "a multisig request executed below threshold." Concretely, funds can be moved by the multisig with fewer than `num_confirmations` live signers actually agreeing, defeating the k-of-n custody guarantee the contract is meant to enforce.

### Likelihood Explanation
No privileged action is required from the attacker beyond being one of the ordinary multisig members who happens to also submit the member-removal request — a normal governance action (removing a compromised/departing key) that multisig owners routinely perform. The race only requires: (1) a pending request already partially confirmed by the soon-to-be-removed member, and (2) a `DeleteMember`/`DeleteKey` request executing before that pending request is confirmed to completion. Because pending requests can persist for the `REQUEST_COOLDOWN` window (15 minutes) and there is no enumeration/cleanup of confirmations across all requests on member removal, this is straightforward to trigger deliberately or to hit accidentally in normal multisig operation.

### Recommendation
On member/key removal, iterate over **all** pending requests' confirmation sets (not just those authored by the removed member) and strip the removed member's entry from each, e.g. re-validate `confirmations.len()` against `num_confirmations` after filtering out non-members, or eagerly purge the removed member's id from every `confirmations` entry in the `DeleteMember`/`DeleteKey` handlers, mirroring the existing "remove requests where `member == removed`" logic in `multisig2::delete_member` (`multisig2/src/lib.rs:356-379`) and `multisig::DeleteKey` handling (`multisig/src/lib.rs:198-216`) but applied to the confirmation sets of *other* requests as well. Alternatively, `confirm()`/`execute_request()` should re-validate that every entry in the confirmation set still belongs to `self.members` (multisig2) or still has an active key (multisig) before counting it toward the threshold.

### Proof of Concept
Using `multisig2` semantics (analogous for `multisig`):
1. Initialize with `num_confirmations = 3` and members `A, B, C, D` (`MultiSigContract::new`, `multisig2/src/lib.rs:148-167`).
2. `A.add_request(R1 = Transfer{...})` — creates request `R1` with 0 confirmations (`add_request`, `multisig2/src/lib.rs:170-200`).
3. `B.confirm(R1)` → confirmations = `{B}` (len 1, `1+1<3`, no execution).
4. `C.confirm(R1)` → confirmations = `{B, C}` (len 2, `2+1<3`, no execution).
5. Separately, `A, B, D` confirm and execute `R2 = DeleteMember{member: C}` (3 confirmations reached, executes via `delete_member`, `multisig2/src/lib.rs:356-379`). `C` is now removed from `self.members`; `R1`'s confirmation set `{B, C}` is untouched because `R1` was authored by `A`, not `C`.
6. `A.confirm(R1)` → `confirm()` reads `confirmations.get(&R1)` = `{B, C}`, computes `2 + 1 >= 3` → true → `execute_request(R1)` runs the `Transfer` (`multisig2/src/lib.rs:294-315`).

Result: `R1` executes with approvals from `A` and `B` plus a stale approval credited to `C`, who is no longer a member — i.e., the transfer executes with only 2 live-member confirmations against a configured 3-of-n threshold.

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
