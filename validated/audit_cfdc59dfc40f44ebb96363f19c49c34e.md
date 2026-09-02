### Title
Multisig request can execute below the live-member confirmation threshold after a confirming member is deleted - (multisig2/src/lib.rs)

### Summary
`delete_member` in `multisig2/src/lib.rs` only purges *pending requests originated by* the removed member, but never purges that member's *confirmations* recorded on other still-pending requests. `confirm` then counts the size of the stale confirmations set against the live `num_confirmations` threshold, so a request can be executed with fewer distinct live-member approvals than the configured threshold.

### Finding Description
The binding the multisig is supposed to enforce is:
```
confirmations counted on execution == approvals from currently active members >= num_confirmations
```

`confirm()` checks this purely by set cardinality: [1](#0-0) 

Confirmations are recorded by member identity string (`member.to_string()`), and this set is never revalidated against the current `members` set at confirm/execute time.

`delete_member()` only cleans up requests that were *authored* by the removed member (`r.member == member`); confirmations that member gave on *other* pending requests (authored by someone else) are left untouched in `self.confirmations`: [2](#0-1) 

Consequently:
- Before: a pending request `R` (authored by member A) has confirmations `{B, D}` while members = `{A,B,C,D}`, `num_confirmations = 3`.
- Attacker/committee removes member `D` via a separate, properly-confirmed `DeleteMember` request. `delete_member` only checks `members.len() - 1 >= num_confirmations` (3 ≥ 3, passes) and removes `D` from `members`, but does **not** touch `R`'s confirmation set, which still contains `"D"`.
- Now live members = `{A,B,C}`, threshold is still 3.
- Member `C` confirms `R`: `confirmations.len() + 1 = 2 (B,D) + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires.

`R` executes having received live approval from only `B` and `C` (2 of 3 live members), while the stale confirmation from the now-removed `D` fills in for the third required signature. This breaks the equality "confirmations counted == live approvals ≥ threshold," letting an action (e.g. `Transfer`, `AddKey`, `FunctionCall`) execute below the intended threshold.

### Impact Explanation
This maps to the explicit Critical impact category "a multisig request executed below threshold." Any action type supported by `execute_request` — including `Transfer` of the account's NEAR balance, `AddKey` (granting a full-access key), or `FunctionCall` — can be pushed through with fewer live approvals than the multisig's configured `num_confirmations`, undermining the entire security guarantee of the K-of-N scheme and enabling unauthorized movement of funds held by the multisig account.

### Likelihood Explanation
Likelihood is realistic in normal multisig operation: membership changes (via `DeleteMember`) are a routine, permitted, unprivileged-within-the-scheme operation (any K members can approve it), and having other requests pending concurrently with a confirmation from the soon-to-be-removed member is a common real-world sequence (e.g., a member confirms multiple pending requests before being rotated out for key-rotation/compromise reasons). No foundation, contract owner, or out-of-scope actor is required — only the multisig members who already control the account per the documented K-of-N model, exploiting an accounting gap rather than any external trust.

### Recommendation
When a member is deleted, iterate over all pending `requests`/`confirmations` and strip the deleted member's identity from every confirmation set (not just requests it authored), or equivalently, re-validate at `confirm()`/execution time that every entry in the counted confirmation set still belongs to `self.members` before comparing the count to `num_confirmations`.

### Proof of Concept
Using the existing test harness in `multisig2/src/lib.rs` (members A,B,C,D; `num_confirmations = 3`):
1. As member A, call `add_request` with a `Transfer` request `R` targeting an attacker account.
2. As member B, call `confirm(R)` → confirmations = `{B}`.
3. As member D, call `confirm(R)` → confirmations = `{B, D}` (2 < 3, pending).
4. As members A, B, C, create and confirm a `DeleteMember { member: D }` request (3 confirmations, meets threshold) → executes `delete_member`, removing D from `members`. Note `delete_member` (`multisig2/src/lib.rs:355-379`) does not touch `R`'s confirmations.
5. As member C, call `confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations (3)` → `execute_request(R)` runs and the `Transfer` executes, despite only B and C (2 of the now 3 live members) having actually approved `R`. [3](#0-2)

### Citations

**File:** multisig2/src/lib.rs (L224-243)
```rust
    fn execute_request(&mut self, request: MultiSigRequest) -> PromiseOrValue<bool> {
        let mut promise = Promise::new(request.receiver_id.clone());
        let receiver_id = request.receiver_id.clone();
        let num_actions = request.actions.len();
        for action in request.actions {
            promise = match action {
                MultiSigRequestAction::Transfer { amount } => promise.transfer(amount.into()),
                MultiSigRequestAction::CreateAccount => promise.create_account(),
                MultiSigRequestAction::DeployContract { code } => {
                    promise.deploy_contract(code.into())
                }
                MultiSigRequestAction::AddMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.add_member(promise, member)
                }
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
                }
                MultiSigRequestAction::AddKey {
```

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
