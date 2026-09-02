## Analysis

I have confirmed a valid analog matching the "confirmations counted versus live members" custody-binding hint in `multisig2/src/lib.rs`.

### Title
Stale Confirmations From Removed Members Still Count Toward Execution Threshold Enabling Sub-Threshold Request Execution - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only purges requests *authored* by the removed member, but never scrubs that member's confirmation entries recorded on requests authored by *other*, still-active members. `confirm` then tallies `confirmations.len()` against `num_confirmations` without validating that every entry in the confirmations set still corresponds to a current member, so a former member's stale signature can supply one of the votes needed to execute a transfer, key addition, or contract deployment.

### Finding Description
`confirm` reads the confirmation set for a request and executes as soon as `confirmations.len() as u32 + 1 >= self.num_confirmations`, with no re-validation that each address/key already in the set is still in `self.members`: [1](#0-0) 

`delete_member` removes the departing member from `self.members` and deletes only the requests where `r.member == member` (i.e., requests the removed member *created*), clearing confirmations solely for those requests: [2](#0-1) 

It never iterates `self.confirmations` to strip the removed member's votes from requests created by *other* members. `current_member()` is only used to authorize new confirmations/requests going forward — it is not used to re-validate previously recorded confirmations: [3](#0-2) 

The intended custody binding is: `count of confirmations on a request == count of confirmations by accounts currently in self.members`. After a `DeleteMember` request executes, this equality breaks — a removed member's earlier confirmation remains embedded in the `HashSet<String>` and is still counted by `confirm`'s threshold check.

### Impact Explanation
In a K-of-N multisig, if a member is removed (e.g., because their key was compromised, they left the organization, or the group wants to reduce trust in them) while they have an outstanding confirmation on a pending `Transfer`, `AddKey`, `DeployContract`, or `FunctionCall` request, that stale confirmation still counts toward `num_confirmations`. This lets the remaining members execute a request with effectively fewer than K *live* signers — a governance/threshold violation. In the worst case, an attacker who is about to be removed can pre-confirm a malicious pending request (e.g., a `Transfer` draining funds, or `AddKey` granting themselves a new full-access key) so that after their removal, one fewer live signature is needed, allowing collusion with a minority of remaining members to move funds or add persistent access below the documented threshold. This matches the report's "Critical: a multisig request executed below threshold" impact category.

### Likelihood Explanation
Likelihood is moderate: it requires (1) a request pending confirmation at the time a member is removed, and (2) the removed member having already confirmed it. This is a realistic operational sequence — member removal typically happens in response to key compromise or loss of trust, precisely the scenario where an in-flight malicious confirmation is most plausible. No privileged access beyond being (formerly) a legitimate member is needed to plant the stale confirmation; execution can then be completed by any remaining members reaching `num_confirmations - 1` additional legitimate confirmations.

### Recommendation
When executing `DeleteMember`, iterate all pending requests' confirmation sets and strip the removed member's identifier, not just the requests that member authored. Alternatively, in `confirm` (and ideally before execution), filter `confirmations` to only members currently present in `self.members` before comparing against `num_confirmations`, ensuring the threshold is always evaluated against live membership.

### Proof of Concept
1. Contract initialized with 3 members (A, B, C) and `num_confirmations = 2`.
2. Member A calls `add_request` to create a `Transfer` request to a malicious receiver, then confirms it (`add_request_and_confirm`) → confirmations = {A}.
3. Members B and C (as legitimate multisig action) submit and confirm a `DeleteMember { member: A }` request to remove A (e.g., due to suspected compromise) — this executes via `delete_member`, per [4](#0-3) , which only removes requests *authored* by A; the Transfer request from step 2 was authored by A, so in this exact sequence it would be purged — **but** if A had instead confirmed a request originally created by B or C, that confirmation is never cleaned.
4. Corrected PoC: Member B creates the malicious `Transfer` request. Member A (compromised) confirms it → confirmations = {A}. Members B/C then execute `DeleteMember{A}` to remove A — `delete_member` only deletes requests where `r.member == member`; since the Transfer request's `member` field is B, it is **not** deleted, and A's confirmation entry remains in `self.confirmations`.
5. Now only 2 members remain (B, C), `num_confirmations` is still 2. Member C calls `confirm` on the Transfer request: `confirmations.len() as u32 + 1 (=1+1=2) >= num_confirmations (2)` → true, so the request executes using A's stale confirmation plus C's, even though A is no longer a member — the transfer was authorized with only one live confirming member (C) instead of two. [1](#0-0) [2](#0-1)

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
