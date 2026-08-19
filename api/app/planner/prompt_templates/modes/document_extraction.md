# Document Extraction Mode

## Purpose
Extract ALL required charter entities from uploaded documents in a single pass.

---

## Extraction Targets
Extract the topics listed in the request context.
Use the provided topic IDs as JSON keys.

Special cases:
- If "goal" is present, return it as a single string.
- If "scope" is present, return "scope_items" and "out_of_scope_items".
For all other topics, return arrays keyed by the topic id.

---

## Response Format
Return ONLY valid JSON:

Return ONLY valid JSON that matches the requested schema.

---

## Extraction Rules
1. **Be specific** - Extract actual content, not placeholders
2. **Preserve intent** - Match document's language and terminology
3. **Skip empty** - If nothing found for a topic, use empty array
4. **No hallucination** - Only extract what's explicitly in the document
