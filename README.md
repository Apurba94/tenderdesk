# TenderDesk

Keep procurement fair and easy to audit, offline. It records requests, quotes and panel marks, scores bids against weighted criteria, and keeps a trail that shows if anyone changed it.

```
python -m tenderdesk
```

Then open http://127.0.0.1:8047. It needs Python 3.10 or newer.

## What it does

- **Buying method by value:** direct purchase, quotations or open tender. The thresholds are placeholders, so set your own policy in `THRESHOLDS`
- **Weighted scoring:** price points by the common formula (lowest price divided by this price), with averaged panel marks for the rest
- **Compliance:** a bid that fails requirements is shown but cannot win, and cannot set the lowest price
- **Conflict of interest:** a member who declares one is blocked from marking that request
- **Points to check:** too few quotes, prices suspiciously close, a price far below the rest, all prices over the estimate, and large panel disagreements. These prompt a question, and none of them proves wrongdoing
- **Close results:** a sensitivity check shows if small weight changes would change the winner
- **Awards need reasons:** passing over the top bid needs a written reason, and a draft award note is provided

## The audit trail

Each action is one line, and each line includes a SHA-256 hash of the line before it. Change, remove or reorder an old line, and the page reports where the chain breaks.

**This detects tampering, it does not prevent it.** Someone with full access to the file could rebuild the whole chain, so to make that detectable, copy the latest hash somewhere safe from time to time, such as in signed minutes.

## How it is tested

Scoring is tested on worked examples, including bids that are not compliant. Each red flag has a case, and a normal set must raise none. The chain is tested against edits, deletions, reordering and forged lines, and app tests confirm the trail catches a direct database edit.

## Limits

It does not replace your procurement policy or legal advice. It handles one organisation on one computer, with no vendor portal, sealed bids or contract tracking. Bids entered before the deadline can be read by anyone with the file.

## Tests

```
python -m unittest discover -s tests
```

MIT licence.
