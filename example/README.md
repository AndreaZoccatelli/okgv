# Example: Intent Classification Dataset

Synthetic training data for fine-tuning LLM intent classifiers. Each entry is a short user utterance (~20 tokens) with an intent label and difficulty level.

## Setup

```bash
cd example
# Edit .env with your connection details
okgv create-structure --file topics.json
```

## Entry format

```json
{
  "utterance": "I want my money back for order #4521",
  "intent": "request_refund",
  "difficulty": "easy"
}
```

Fields:
- `utterance` — short user message (required)
- `intent` — intent label (required)
- `difficulty` — `easy`, `medium`, or `hard` (default: `medium`)

Easy = clear intent, no ambiguity. Hard = implicit intent, slang, typos, multi-intent.

## Agent workflow

```bash
# 1. Find topic with fewest entries
okgv least-topic --topic customer_support/billing
# → {"topic": "customer_support/billing/refund", "count": 0, ...}

# 2. Check what coverage gaps exist
okgv topic-stats --topic customer_support/billing/refund --fields "difficulty,intent"

# 3. Generate entry, check similarity
okgv similar --topic customer_support/billing/refund \
  --entry '{"utterance": "I need a refund please", "intent": "request_refund", "difficulty": "easy"}'

# 4. Submit if novel enough
okgv submit --topic customer_support/billing/refund \
  --entry '{"utterance": "I need a refund please", "intent": "request_refund", "difficulty": "easy"}'

# 5. Batch submit multiple entries
okgv submit-batch --topic customer_support/billing/refund --entries '[
  {"utterance": "Can I get my money back?", "intent": "request_refund", "difficulty": "easy"},
  {"utterance": "ugh this thing broke already smh want refund", "intent": "request_refund", "difficulty": "hard"},
  {"utterance": "I would like to inquire about the possibility of reversing my recent transaction", "intent": "request_refund", "difficulty": "medium"}
]'
```

## Topic hierarchy

```
customer_support/
├── billing/          (refund, payment_issue, subscription)
├── account/          (login_problem, profile_update, delete_account)
└── product/          (bug_report, feature_request, how_to)

commerce/
├── order/            (track_order, cancel_order, change_order)
├── shipping/         (delivery_time, shipping_cost, international)
└── returns/          (return_policy, exchange, damaged_item)
```

24 leaf topics. Agent fills each with diverse utterances across difficulty levels.
