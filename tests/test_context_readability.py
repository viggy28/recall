import unittest

from recall_core.context_creation import initial_draft_size_error
from recall_core.context_policy import (
    READING_BOUNDARY, add_creation_provenance, blank_context,
    metadata_fields, prepare_update, structure_instruction, validate_context,
)
from tests.test_context_policy import DOCUMENT as V1_DOCUMENT


DOCUMENT = """<!-- recall-context: 2 -->
# Core DB reader restarts

## Summary
Both readers restarted. AWS reported a reclamation problem; the workload remains unknown.

## Open questions and next actions
- Ask AWS which workload prevents reclamation. (Proposed.)

---

Supporting detail follows.

## Current understanding
The records agree on simultaneous restarts and 14–18 second recovery. [E1]

## Details
Read-only savepoints need not receive subtransaction IDs. Writer changes can replicate to readers. [E2]

## Evidence
- **E1** — [Reader logs](https://example.test/logs?long=query&retained=true)
- **E2** — User-provided vendor response; direct case link unavailable.

### Context metadata
- Scope: Reader restart investigation.
- Status: unknown
"""


class ReadableContextTests(unittest.TestCase):
    def test_title_summary_and_next_actions_precede_reference_material(self):
        report = validate_context(DOCUMENT, require_current=True)
        self.assertEqual(report['schema_version'], 2)
        self.assertLess(DOCUMENT.index('## Summary'), DOCUMENT.index('## Open questions'))
        self.assertLess(DOCUMENT.index(READING_BOUNDARY), DOCUMENT.index('## Current understanding'))
        self.assertGreater(DOCUMENT.index('- Scope:'), DOCUMENT.index('## Evidence'))
        self.assertEqual(report['warnings'], [])

    def test_compact_citations_resolve_and_url_targets_stay_in_evidence(self):
        for bad in (
            DOCUMENT.replace('unknown.', 'unknown. https://example.test/support', 1),
            DOCUMENT.replace('unknown.', 'unknown. s3://example-bucket/report', 1),
            DOCUMENT.replace('[E1]', '[E3]', 1),
            DOCUMENT.replace('**E2**', '**E1**'),
            DOCUMENT + '\n[E1]: https://example.test/expanded\n',
            DOCUMENT.replace('[E1]', '[^E1]', 1),
        ):
            with self.subTest(text=bad[:120]), self.assertRaises(ValueError):
                validate_context(bad, require_current=True)

    def test_metadata_is_final_and_required_but_does_not_dominate_the_opening(self):
        for bad in (
            DOCUMENT.replace('# Core DB reader restarts', '> Updated: `2026-01-01`\n# Core DB reader restarts'),
            DOCUMENT.replace('- Scope: Reader restart investigation.\n', ''),
            DOCUMENT + '\n- Status: active\n',
            DOCUMENT.replace('### Context metadata', '### Missing metadata'),
            DOCUMENT.replace('## Current understanding', '- Scope: premature metadata\n\n## Current understanding'),
            DOCUMENT.replace('- Scope: Reader restart investigation.', 'arbitrary footer prose'),
        ):
            with self.subTest(text=bad[:120]), self.assertRaises(ValueError):
                validate_context(bad, require_current=True)

    def test_quick_read_boundary_is_enforced(self):
        with self.assertRaisesRegex(ValueError, 'quick-read boundary'):
            validate_context(DOCUMENT.replace(READING_BOUNDARY, ''))
        without_actions = DOCUMENT.replace('- Ask AWS which workload prevents reclamation. (Proposed.)', '')
        with self.assertRaisesRegex(ValueError, 'next actions must contain'):
            validate_context(without_actions)

    def test_fenced_operational_urls_are_not_treated_as_prose_links(self):
        document = DOCUMENT.replace('## Details\n', '## Details\n```bash\ncurl https://example.test/health\n```\n')
        validate_context(document)

    def test_fenced_evidence_example_does_not_resolve_a_real_citation(self):
        document = DOCUMENT.replace('**E2** — User-provided vendor response; direct case link unavailable.', '**E9** — Different reference.')
        document = document.replace('### Context metadata', '```text\n- **E2** — Example, not an entry.\n```\n\n### Context metadata')
        with self.assertRaisesRegex(ValueError, 'unresolved evidence'):
            validate_context(document)

    def test_generated_provenance_is_added_only_at_the_footer(self):
        generated = add_creation_provenance(DOCUMENT, {
            'Generated': '`2026-01-01T00:00:00Z`',
            'Updated': '`2026-01-01T00:00:00Z`',
            'Evidence through': '`2025-12-31T00:00:00Z`',
            'Source sessions': '`session-example`',
            'Focus': 'Reader restart findings',
        }, ['- [Original session](file:///tmp/original-session.jsonl)'])
        validate_context(generated, require_current=True)
        self.assertGreater(generated.index('- Generated:'), generated.index('## Evidence'))
        self.assertIn('- **E3** — [Original session]', generated)
        self.assertEqual(metadata_fields(generated)['Evidence through'], '`2025-12-31T00:00:00Z`')

    def test_already_linked_source_is_not_appended_a_second_time(self):
        generated = add_creation_provenance(DOCUMENT, {}, [
            '[Selected source](https://example.test/logs?long=query&retained=true)',
        ])
        self.assertEqual(generated.count('https://example.test/logs?long=query&retained=true'), 1)
        self.assertNotIn('**E3**', generated)

    def test_footer_cannot_stand_in_for_evidence(self):
        blank = blank_context('test').replace('No evidence recorded.\n', '')
        with self.assertRaisesRegex(ValueError, 'Evidence must contain'):
            validate_context(blank)

    def test_edits_stamp_only_footer_update_time_not_evidence_quotes(self):
        original = add_creation_provenance(DOCUMENT, {'Updated': '`2026-01-01`', 'Evidence through': '`2025-12-31`'}, [])
        original = original.replace('### Context metadata', '- Updated: `2020-01-01 source quote`\n\n### Context metadata')
        edited = original.replace('workload remains unknown.', 'workload still needs attribution.')
        result = prepare_update(original, edited, now='2026-02-01T00:00:00Z')['text']
        self.assertIn('- Updated: `2020-01-01 source quote`', result)
        self.assertEqual(metadata_fields(result)['Updated'], '`2026-02-01T00:00:00Z`')
        self.assertEqual(metadata_fields(result)['Evidence through'], '`2025-12-31`')

    def test_v1_can_still_be_read_and_updated_but_never_silently_converted(self):
        self.assertEqual(validate_context(V1_DOCUMENT)['schema_version'], 1)
        updated = prepare_update(V1_DOCUMENT, V1_DOCUMENT.replace('metadata accumulated.', 'metadata accumulated over days.'))['text']
        self.assertTrue(updated.startswith('<!-- recall-context: 1 -->'))
        with self.assertRaisesRegex(ValueError, 'change the context schema'):
            validate_context(DOCUMENT, original=V1_DOCUMENT)
        self.assertIn('new contexts require format v2', initial_draft_size_error(V1_DOCUMENT))

    def test_blank_and_prompt_use_the_same_readable_layout(self):
        blank = blank_context('core-db-reader-restart')
        validate_context(blank, require_current=True)
        self.assertIn('# Core db reader restart', blank)
        instruction = structure_instruction('core-db-reader-restart-readable-preview')
        self.assertIn('human-readable title', instruction)
        self.assertIn('two to four', instruction)
        self.assertIn('plain-text markers', instruction)
        self.assertIn('proposed or agreed', instruction)
