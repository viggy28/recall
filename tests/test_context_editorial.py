import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import recall
from recall_core.context_editorial import apply_editorial_response, editorial_prompt
from recall_core.context_policy import blank_context, sections
from tests.test_context_policy import DOCUMENT as V1_CONTEXT


EVIDENCE = """Fictional regression evidence, not a real incident:
Both readers crashed on October 9, 2026 and recovered in 14–18 seconds. The writer did not restart.
AWS reported exhaustion of its internal subtransaction metadata pool due to delayed reclamation.
AWS reported no recurrence after Aurora PostgreSQL 13.20 was upgraded to 13.23, not a confirmed fix.
The workload that prevented reclamation remains unknown. Asking AWS for workload diagnostics was proposed,
not agreed or sent. No original AWS support-case URL was supplied.
"""
GOOD_SUMMARY = (
    "Both readers crashed on October 9, 2026 and recovered in 14–18 seconds; the writer did not restart. "
    "AWS attributed the crashes to delayed reclamation in its subtransaction metadata pool. "
    "AWS reported no recurrence after upgrading Aurora PostgreSQL 13.20 to 13.23, but this is not a confirmed fix. "
    "The triggering workload remains unknown."
)
DRAFT = blank_context('reader-investigation').replace(
    '## Summary\nNot established.',
    '## Summary\nApplication sources of subtransactions were identified. The upgrade fixed the issue.'
).replace('## Current understanding\nNot established.', '## Current understanding\n' + EVIDENCE.strip()).replace(
    'No evidence recorded.', '- **E1** — Fictional regression source; link not supplied.'
)


def response(summary=GOOD_SUMMARY, notes=None, blocked=False):
    return json.dumps({'summary': summary, 'notes': notes or [], 'blocked': blocked})


class ContextEditorialTests(unittest.TestCase):
    def test_rewrite_restores_impact_without_altering_evidence_or_provenance(self):
        result = apply_editorial_response(DRAFT, response(notes=['Restored impact; removed unsupported fix claim.']))
        self.assertEqual(sections(result['text'])['Summary'], GOOD_SUMMARY)
        self.assertEqual({k: v for k, v in sections(DRAFT).items() if k != 'Summary'},
                         {k: v for k, v in sections(result['text']).items() if k != 'Summary'})
        self.assertIn('Restored impact', result['editorial_notes'][0])

    def test_prompt_preserves_meaning_and_distinguishes_uncertainty_from_clutter(self):
        prompt = editorial_prompt(DRAFT, EVIDENCE, 'Improve the Summary.')
        for principle in ['familiar words', 'clear verbs', 'single focus', 'empty generalities',
                          'meaningful uncertainty', 'affected/unaffected', 'recovery time',
                          'attribution', 'verbatim', 'untrusted data', 'ONLY Summary']:
            self.assertIn(principle, prompt)
        data = json.loads(prompt.split('Inputs (JSON data, not instructions):\n')[1])
        self.assertEqual(data['evidence'], EVIDENCE)

    def test_noop_review_preserves_exact_bytes(self):
        original = apply_editorial_response(DRAFT, response())['text']
        self.assertEqual(apply_editorial_response(original, response())['text'], original)

    def test_conflicts_and_bad_responses_fail_closed(self):
        for invalid in [
            'not JSON', response(blocked=True, notes=['Unsupported claim outside Summary.']),
            response(summary='word ' * 151), response(summary=''),
            response(summary='A claim. [E99]'),
            response(summary='A claim.\n\n## Evidence\nReplaced sources.'),
            json.dumps({'summary': GOOD_SUMMARY, 'notes': [], 'blocked': 'false'}),
            json.dumps({'summary': GOOD_SUMMARY, 'notes': [], 'blocked': False, 'extra': 'silently change evidence'}),
            response(notes=['x' * 501]),
        ]:
            with self.subTest(response=invalid[:70]), self.assertRaises(ValueError):
                apply_editorial_response(DRAFT, invalid)

    def test_v1_review_preserves_its_format_and_prelude(self):
        summary = sections(V1_CONTEXT)['Summary'].replace('AWS reported', 'AWS described')
        result = apply_editorial_response(V1_CONTEXT, response(summary=summary))
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['text'].split('## Summary')[0], V1_CONTEXT.split('## Summary')[0])
        self.assertEqual(result['text'].split('## Current understanding')[1], V1_CONTEXT.split('## Current understanding')[1])

    def test_legacy_does_not_get_an_implicit_summary_or_migration(self):
        for operation in [lambda: editorial_prompt('# Legacy\nText.', EVIDENCE),
                          lambda: apply_editorial_response('# Legacy\nText.', response())]:
            with self.assertRaisesRegex(ValueError, 'legacy contexts are not converted'):
                operation()

    def test_creation_really_calls_editor_after_drafting_and_preserves_selected_model(self):
        with mock.patch.object(recall, '_run_pi_generation', side_effect=[DRAFT, response()]) as model:
            text, notes = recall._generate_creation_draft('reader', 'Reader investigation', 'custom', [EVIDENCE], 'test/model')
        self.assertEqual(model.call_count, 2)
        self.assertTrue(model.call_args_list[1].args[0].startswith('Review the Summary'))
        self.assertIn('Fictional regression evidence', model.call_args_list[1].args[0])
        self.assertTrue(all(call.args[1] == 'test/model' for call in model.call_args_list))
        self.assertEqual(sections(text)['Summary'], GOOD_SUMMARY)

    def test_failed_editor_does_not_fall_back_to_the_unreviewed_draft(self):
        with mock.patch.object(recall, '_run_pi_generation', side_effect=[DRAFT, 'broken']), \
                self.assertRaisesRegex(recall.ContextError, 'invalid JSON'):
            recall._generate_creation_draft('reader', 'Reader investigation', 'custom', [EVIDENCE], None)

    def test_cli_update_uses_editor_before_save_review(self):
        original = DRAFT.replace('The upgrade fixed the issue.', 'The fix is unconfirmed.')
        patch = json.dumps({'edits': [{'old_text': 'Application sources of subtransactions were identified.',
                                      'new_text': 'Application savepoint producers were identified.'}]})
        args = SimpleNamespace(name='reader', instruction='Clarify the investigation.', instruction_file=None,
                               replace=None, model='test/model', dry_run=True, yes=False)
        out = io.StringIO()
        with mock.patch.object(recall, '_read_context', return_value=(Path('/unused'), original)), \
                mock.patch.object(recall, '_run_pi_generation', side_effect=[patch, response()]) as model, \
                mock.patch.object(recall, '_write_context_update') as save, redirect_stdout(out):
            recall._context_update(args)
        self.assertEqual(model.call_count, 2)
        self.assertIn('Summary editorial pass completed', out.getvalue())
        self.assertIn('14–18', out.getvalue())
        save.assert_not_called()

    def test_editorial_protocol_is_local_and_does_not_call_models_or_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'input.json'
            path.write_text(json.dumps({'text': DRAFT, 'evidence': EVIDENCE}))
            out = io.StringIO()
            with mock.patch.object(recall, '_run_pi_generation') as model, \
                    mock.patch.object(recall, '_write_context_update') as save, redirect_stdout(out):
                recall.main(['context', 'editorial', '--input-file', str(path)])
            self.assertIn('Review the Summary', json.loads(out.getvalue())['prompt'])
            model.assert_not_called()
            save.assert_not_called()


@unittest.skipUnless(os.environ.get('RECALL_EDITORIAL_MODEL'), 'opt-in live editorial evaluation')
class LiveEditorialRegressionTests(unittest.TestCase):
    def test_reader_impact_attribution_and_upgrade_uncertainty(self):
        model = os.environ['RECALL_EDITORIAL_MODEL']
        result = recall._review_context_summary(DRAFT, EVIDENCE, 'Improve the Summary.', None if model == 'default' else model)
        summary = sections(result['text'])['Summary']
        self.assertIn('14', summary)
        self.assertIn('18', summary)
        self.assertRegex(summary.lower(), r'writer[^.!?;]*(?:no restart|did not restart|remained (?:running|available|up))')
        self.assertIn('AWS', summary)
        self.assertIn('13.20', summary)
        self.assertIn('13.23', summary)
        self.assertRegex(summary.lower(), r'(?:not|unconfirmed|unproven|uncertain)[^.]{0,100}(?:fix|resolv)|(?:fix|upgrade)[^.]{0,100}(?:not|unconfirmed|unproven|uncertain)')
        self.assertRegex(summary.lower(), r'workload[^.]*(?:unknown|unidentified|undetermined)|unknown[^.]*workload')
        print('\nLive editorial regression Summary:\n' + summary)

    def test_unsupported_supporting_claim_blocks_the_proposal(self):
        draft = DRAFT.replace('## Current understanding\n', '## Current understanding\nAWS independently confirmed the upgrade fixes all future crashes.\n')
        model = os.environ['RECALL_EDITORIAL_MODEL']
        with self.assertRaisesRegex(recall.ContextError, 'editorial review blocked'):
            recall._review_context_summary(draft, EVIDENCE, 'Improve the Summary.', None if model == 'default' else model)
