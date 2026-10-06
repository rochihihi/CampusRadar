import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from main import app
from storage import connect, now
from rag import retrieve, sync_index


class RagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get('CAMPUSRADAR_DATA_DIR')
        os.environ['CAMPUSRADAR_DATA_DIR'] = self.tmp.name
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()
        if self.previous is None:
            os.environ.pop('CAMPUSRADAR_DATA_DIR', None)
        else:
            os.environ['CAMPUSRADAR_DATA_DIR'] = self.previous

    def notice(self, nid, title, content, version='2'):
        with connect() as db:
            db.execute('INSERT INTO notices(id,external_id,title,url,content,content_version,first_seen,updated_at) VALUES(?,?,?,?,?,?,?,?)', (nid, nid, title, 'https://example.edu/'+nid, content, version, now(), now()))

    def test_retrieves_end_of_long_document(self):
        self.notice('n', '学生通知', '课程安排说明。\n'*3000+'\n奖学金申请需要提交成绩单和申请表。')
        hits = retrieve('奖学金申请需要哪些材料')
        self.assertTrue(any('提交成绩单和申请表' in h['excerpt'] for h in hits))
        self.assertGreater(sync_index()['chunks'], 4)

    def test_index_updates_deletes_and_excludes_placeholders(self):
        self.notice('n', '奖学金', '奖学金申请请准备成绩单。')
        self.notice('placeholder', '竞赛报名', '竞赛报名', '0')
        self.assertEqual(sync_index()['documents'], 1)
        self.assertEqual(retrieve('竞赛报名'), [])
        with connect() as db:
            db.execute("UPDATE notices SET title='微专业',content='微专业报名需提供申请表。' WHERE id='n'")
        self.assertEqual(retrieve('奖学金申请'), [])
        self.assertTrue(retrieve('微专业报名'))
        with connect() as db:
            db.execute("DELETE FROM notices WHERE id='n'")
        self.assertEqual(sync_index()['chunks'], 0)

    def test_no_hit_does_not_call_model(self):
        with patch('rag.model_json') as model:
            result = self.client.post('/api/rag/ask', json={'question': '奖学金申请需要什么材料'})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['grounded'])
        model.assert_not_called()

    def test_valid_citation_and_rejects_invented_evidence(self):
        self.notice('n', '奖学金申请', '奖学金申请需要提交成绩单。')
        raw = {'claims': [
            {'text': '需要成绩单。', 'citations': [{'id': 'S1', 'quote': '奖学金申请需要提交成绩单。'}]},
            {'text': '不需要任何材料。', 'citations': [{'id': 'S999', 'quote': '不需要材料'}]},
            {'text': '明天截止。', 'citations': [{'id': 'S1', 'quote': '截止日期是明天。'}]}
        ]}
        with patch('rag.model_json', return_value=raw) as model:
            result = self.client.post('/api/rag/ask', json={'question': '奖学金申请材料'})
        data = result.json()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(data['claims']), 1)
        self.assertEqual(data['sources'][0]['url'], 'https://example.edu/n')
        self.assertIn('passages', model.call_args.args[1])

    def test_scope_and_question_validation(self):
        self.notice('n', '奖学金申请', '奖学金申请需要提交成绩单。')
        self.assertEqual(retrieve('奖学金申请', 'unrelated'), [])
        self.assertEqual(self.client.post('/api/rag/ask', json={'question': ' '}).status_code, 422)
        self.assertEqual(retrieve('" OR *'), [])


if __name__ == '__main__':
    unittest.main()
