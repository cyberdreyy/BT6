Confirmed: the same pattern exists in both `multisig/src/lib.rs` (`DeleteKey`) and `multisig2/src/lib.rs` (`DeleteMember`) — deletion only purges confirmations for requests *created by* the removed key/member, never confirmations *cast by* that key/member on requests created by others. I have enough evidence to finalize the analog.

### Title
Stale confirmations from a deleted multisig member/key still count toward `num_confirmations`, allowing execution of a request below the live-member threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` executes a pending request once `confirmations.len() + 1 >= num_confirmations`, treating every entry ever inserted into the request's `confirmations` set as a valid, currently-authorized approval [1](#0-0) . However, `delete_member` (and its `multisig/src/lib.rs` analog `DeleteKey`) only scrubs requests that were *created* by the removed member — it never removes that member's stale confirmations from other pending requests they had merely confirmed [2](#0-1) . As a result, a request can later be executed by combining a removed member's stale confirmation with fewer live-member confirmations than `num_confirmations` actually requires.

### Finding Description
The custody binding a K-of-N multisig must uphold is: `confirmations counted == confirmations from currently-authorized members`. This is exactly the equality broken here.

- `add_request`/`confirm` insert the calling member's identifier (`MultisigMember::to_string()`) into a `HashSet<String>` keyed by `request_id` [3](#0-2) .
- `delete_member`, invoked when a `DeleteMember` request is itself confirmed by K members, removes the member from `self.members` and deletes its access key, but only cleans up `self.requests`/`self.confirmations` for requests where `r.member == member`, i.e., requests the removed member *authored* — not requests they merely *confirmed* [2](#0-1) .
- `confirm`'s threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` has no notion of "current membership" for entries already stored in the set — it just counts strings [4](#0-3) .

Before the attacker's/triggering call: request R1 (created by member A) has confirmations `{A, B}` with `num_confirmations = 3` and members `{A,B,C,D}` — 2 live confirmations out of 3 required.
After member B is removed (via a legitimately-confirmed `DeleteMember{B}` request from A, C, D): members become `{A,C,D}`, but R1's confirmations remain `{A, B}` unchanged (B's confirmation is never purged because R1 was authored by A, not B).
When C now calls `confirm(R1)`: `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → R1 executes, even though only `{A, C}` — 2 distinct currently-live members — actually approved it. The binding "requests execute only with K live-member confirmations" is violated; the request executes with a stale, no-longer-authorized confirmation counted as if it were live.

### Impact Explanation
This is a direct instance of "a multisig request executed below threshold," listed as Critical impact: `pushVaultAmounts`-style counters (here, `confirmations.len()`) diverge from the actual set of currently entitled approvers. Any pending request (including `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, etc.) can be pushed through execution using one fewer live approval than the configured K, undermining the entire security guarantee the multisig is designed to provide (K-of-N control over the account's funds and permissions).

### Likelihood Explanation
No malicious intent or special privilege is required beyond normal, permitted multisig operations: a request can be left partially confirmed for up to the `REQUEST_COOLDOWN`/active-request lifetime while membership changes occur through ordinary, fully-authorized `DeleteMember`/`DeleteKey` operations (e.g., rotating out a compromised or departing member) — an entirely realistic operational sequence. The bug manifests automatically the next time any live member confirms the older pending request; no special ordering trick beyond usual concurrent request handling is needed.

### Recommendation
When a member/key is deleted, scrub that member's identifier from every entry in the `confirmations` map (not only requests they authored), or validate confirmers against `self.members` inside `confirm`'s threshold check before counting them (filter `confirmations` to currently valid members before comparing against `num_confirmations`).

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request(R1)` (e.g., `Transfer` to some receiver); `A.confirm(R1)` → `confirmations(R1) = {A}`; `B.confirm(R1)` → `confirmations(R1) = {A, B}` (2/3, not yet executed) [3](#0-2) .
3. `C.add_request_and_confirm(R2)` where `R2 = DeleteMember{B}`; `D.confirm(R2)`; `A.confirm(R2)` → 3/3 confirmations reached → `execute_request` runs `delete_member(B)`, removing B from `self.members` and deleting B's key, but leaves `confirmations(R1) = {A, B}` untouched since R1 was authored by A [2](#0-1) .
4. `C.confirm(R1)`: `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → R1 executes, transferring funds/executing the action, despite only A and C (2 of the current 3 members) having live, valid confirmations on it. [1](#0-0) [5](#0-4)

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

**File:** multisig2/src/lib.rs (L341-379)
```rust
    /// Add member to the list. Adds access key if member is key based.
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }

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
