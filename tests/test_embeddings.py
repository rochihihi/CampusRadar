import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from main import app
from storage import connect,now
from rag import sync_index,retrieve_detail
from embeddings import build_batch,save_config,config,embed,normalize


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.previous=os.environ.get('CAMPUSRADAR_DATA_DIR')
        os.environ['CAMPUSRADAR_DATA_DIR']=self.tmp.name
        self.client=TestClient(app)
        with connect() as db:
            db.execute("INSERT INTO notices(id,external_id,title,url,content,content_version,first_seen,updated_at) VALUES('n','n','助学金申报','https://example.edu/n','家庭经济困难学生可以申请助学金。','2',?,?)",(now(),now()))
        sync_index()
        save_config({'enabled':True,'base_url':'https://example.com/v1','model':'test-embedding','api_key':'private-test-key','use_openai_key':False,'threshold':0.35})

    def tearDown(self):
        self.client.close();self.tmp.cleanup()
        if self.previous is None:os.environ.pop('CAMPUSRADAR_DATA_DIR',None)
        else:os.environ['CAMPUSRADAR_DATA_DIR']=self.previous

    def test_semantic_hit_without_keyword_overlap_and_scope(self):
        with patch('embeddings.embed',return_value=[[1.0,0.0]]):
            self.assertEqual(build_batch()['built'],1)
            result=retrieve_detail('手头拮据求帮助')
            self.assertEqual(result['retrieval_mode'],'hybrid')
            self.assertEqual(result['sources'][0]['retrieved_by'],['vector'])
            self.assertEqual(retrieve_detail('手头拮据求帮助','wrong')['sources'],[])

    def test_cache_reuse_invalidation_and_model_change(self):
        with patch('embeddings.embed',return_value=[[1.0,0.0]]) as request:
            build_batch();build_batch()
            self.assertEqual(request.call_count,1)
            with connect() as db:db.execute("UPDATE notices SET content='家庭经济困难学生请提交申请表。' WHERE id='n'")
            sync_index();self.assertEqual(build_batch()['built'],1)
            cfg=config();cfg['model']='other-model';save_config(cfg)
            self.assertEqual(build_batch()['built'],1)

    def test_api_failure_explicit_fallback(self):
        with patch('embeddings.embed',return_value=[[1.0,0.0]]):build_batch()
        with patch('embeddings.embed',side_effect=ValueError('Embedding 权限不足')):
            result=retrieve_detail('助学金申报')
        self.assertEqual(result['retrieval_mode'],'keyword')
        self.assertIn('权限不足',result['retrieval_warning'])
        self.assertTrue(result['sources'])

    def test_embedding_response_order_and_invalid_vectors(self):
        original=httpx.Client
        def handler(request):
            return httpx.Response(200,json={'data':[{'index':1,'embedding':[0,4]},{'index':0,'embedding':[3,0]}]})
        with patch('embeddings.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            self.assertEqual(embed(['first','second'],config()),[[1.0,0.0],[0.0,1.0]])
        for bad in ([0,0],[float('nan'),1],[True,1],[]):
            with self.assertRaises(ValueError):normalize(bad)

    def test_settings_never_return_key_and_prevent_cross_host_reuse(self):
        public=self.client.get('/api/embeddings/settings').json()
        self.assertNotIn('api_key',public)
        self.assertNotIn('private-test-key',str(public))
        cfg=config();cfg.update(api_key='',use_openai_key=True)
        save_config(cfg)
        with connect() as db:db.execute("UPDATE settings SET openai_key='official-secret' WHERE id=1")
        self.assertEqual(config()['api_key'],'')
        self.assertEqual(self.client.put('/api/embeddings/settings',json={'base_url':'http://remote.example/v1'}).status_code,400)
        self.assertEqual(self.client.put('/api/embeddings/settings',json={'threshold':2}).status_code,422)

    def test_dimension_change_requires_rebuild(self):
        with patch('embeddings.embed',return_value=[[1.0,0.0]]):build_batch()
        with patch('embeddings.embed',return_value=[[1.0,0.0,0.0]]):
            result=retrieve_detail('助学金申报')
        self.assertEqual(result['retrieval_mode'],'keyword')
        self.assertIn('维度',result['retrieval_warning'])


if __name__=='__main__':unittest.main()
