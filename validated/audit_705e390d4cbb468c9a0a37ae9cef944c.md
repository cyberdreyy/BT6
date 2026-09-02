### Title
Confirmations from deleted multisig members remain counted toward threshold, allowing requests to execute below the live quorum - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges requests *created* by the removed member, but never scrubs that member's *confirmations* recorded on requests created by other members. Because `confirm()` counts `confirmations.len()` without validating that every recorded confirmer is still a current member, a request can reach `num_confirmations` and execute using a "stale" confirmation from an account that has already been removed from the multisig, effectively executing a request with fewer live-member approvals than the configured threshold.

### Finding Description
The custody/authorization binding the contract must preserve is:
`confirmations counted at execute time == number of confirmations from accounts that are members at execute time`.

`confirm()` enforces the threshold purely by set size: [1](#0-0) 

`delete_member()` removes the member from `self.members` and cleans up:
- requests where `r.member == member` (i.e., requests the deleted member *originated*)
- the member's `num_requests_pk` entry

but it never iterates `self.confirmations` to drop the deleted member's entry from confirmation sets of *other* members' requests: [2](#0-1) 

Consequently, if member `B` confirms a request `R` originated by member `A` (`confirmations = {A, B}`), and `B` is later removed via a separately-confirmed `DeleteMember` request, `R`'s confirmation set still contains `B`. Any subsequently-confirming *live* member increments the count to reach `num_confirmations`, and `execute_request(R)` runs even though only `A` and the new confirmer are actually live members — one fewer live signer than `num_confirmations` requires.

`current_member()` is only used to gate *new* confirmations/requests, not to re-validate historical entries already stored in `self.confirmations`: [3](#0-2) 

`delete_member` also asserts `self.members.len() - 1 >= self.num_confirmations`, so the invariant "members ≥ num_confirmations" is preserved nominally, but this says nothing about whether the confirmations actually recorded on a pending request belong to still-live members: [4](#0-3) 

### Impact Explanation
This breaks the "a multisig request executed below threshold" binding explicitly listed as Critical impact. A `Transfer`, `FunctionCall`, `AddKey`/`AddMember` (granting new signer control), or `DeployContract` (code upgrade) request can be executed with real live-member approval strictly less than `num_confirmations`, undermining the K-of-N security guarantee the contract exists to provide. Funds can move, or the multisig's own control set can be altered, without the number of genuinely authorized live signers required.

### Likelihood Explanation
No privileged actor, redeploy, or external manipulation is required — only the contract's own normal lifecycle: a pending request accumulates partial confirmations, a legitimate `DeleteMember` request executes (removing a signer who had already confirmed a different pending request), and then a normal `confirm()` call from a remaining live member unknowingly pushes a stale-inflated count over the threshold. This is reachable purely through in-scope `confirm`/`add_request`/`DeleteMember` flows in `multisig2/src/lib.rs`, with no test/fixture/tooling dependency.

### Recommendation
When deleting a member, iterate all pending `self.confirmations` entries and remove the member's string from every confirmation set (not just requests they authored). Alternatively, when counting confirmations in `confirm()`, filter the confirmation set to only accounts/keys still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` where `R` transfers funds out — `confirmations(R) = {A}`.
3. `B` calls `confirm(R)` — `confirmations(R) = {A, B}` (still below threshold of 3, not yet executed).
4. A separate request `DeleteMember{member: B}` is created and confirmed by 3 members (`A, C, D`) and executes via `execute_request` → `delete_member(B)`. This only removes requests where `r.member == B` (none, since `B` didn't originate `R`), and only removes `B` from `self.members`; `confirmations(R)` still equals `{A, B}`.
5. `C` (a live member) calls `confirm(R)`. `confirmations(R).len() + 1 = 3 >= num_confirmations(3)` → `execute_request(R)` runs the transfer.
6. Result: `R` executed with confirmations `{A, B, C}`, but `B` was no longer a member at execution time — only 2 live members (`A`, `C`) actually authorized it, one below the configured 3-of-N threshold. [1](#0-0) [2](#0-1)

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
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
