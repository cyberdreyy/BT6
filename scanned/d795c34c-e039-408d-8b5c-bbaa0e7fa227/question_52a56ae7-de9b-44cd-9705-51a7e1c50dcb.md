), then check every handler call site (grep for msg.Body.Sender usage) executes strictly after Validate()/ExtractSigner() and never trusts a client-supplied 'sender' field embedded in Payload.
