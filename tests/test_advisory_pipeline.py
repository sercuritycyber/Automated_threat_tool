import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bin'))
from advisory_pipeline import build, enqueue, flush
from factcheck_advisories import checked
import requests

C={'url':'https://vendor.example/advisory','title':'Fix CVE-2026-12345','summary':'Source reports affected version 1.2.3. Hash: '+('a'*64),'pre_score':80,'source':'Vendor','published':'2026-10-20T12:00:00Z'}
class Tests(unittest.TestCase):
 def test_stable_and_no_inferred_indicators(self):
  a=build(C,None);b=build(C,'2026-10-30T00:00:00Z')
  self.assertEqual(a,b);self.assertEqual(len(a['takeaways']),3);self.assertEqual(a['indicators'],[])
  self.assertEqual(a['cve_ids'],['CVE-2026-12345'])
  self.assertIn('1.2.3',a['source_text'])
 def test_outbox_retry_and_receipt(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);data={'generated_at':C['published'],'candidates':[C,C]}
   enqueue(data,root);self.assertEqual(len(list((root/'documents').glob('*.json'))),1)
   class Down:
    @staticmethod
    def post(*a,**k):raise requests.ConnectionError()
   self.assertEqual(flush(root,'https://test','secret',Down)['failed'],1)
   self.assertFalse((root/'receipts').exists())
   class Up:
    count=0
    @classmethod
    def post(cls,*a,**k):
     cls.count+=1
     class R:
      def raise_for_status(self):pass
      def json(self):return {'id':k['json']['id'],'stored':True}
     return R()
   self.assertEqual(flush(root,'https://test','secret',Up)['sent'],1)
   self.assertEqual(flush(root,'https://test','secret',Up)['sent'],0);self.assertEqual(Up.count,1)
 def test_secret_and_bad_url_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   result=enqueue({'candidates':[{**C,'summary':'secret-token-long-1234'},{**C,'url':'javascript:alert(1)'}]},Path(td),{'API_KEY':'secret-token-long-1234'})
   self.assertEqual(result,{'accepted':0,'rejected':2})
 def test_ai_cannot_fabricate_web_check(self):
  d=build(C,None)
  raw=json.dumps({'checks':[{'id':d['id'],'verdict':'supported','note':'Source checked','evidence_urls':[C['url']]}]})
  with self.assertRaises(ValueError):checked(raw,[d],False)
  self.assertEqual(checked(raw,[d],True)[0]['verdict'],'supported')
  with self.assertRaises(ValueError):checked(raw.replace(d['id'],'unknown'),[d],True)
if __name__=='__main__':unittest.main()
