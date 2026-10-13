"""Run: python -m unittest discover -s tests -v"""
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import daytrace as m

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=m.Store(Path(self.tmp.name)/'test.sqlite3')
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def test_intervals_and_merge(self):
        state={'app':'code.exe','title':'','domain':'','category':'编程'}
        self.store.add_interval(100,105,state)
        self.store.add_interval(105,110,state)
        records=self.store.query(0,200)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['end'],110)
        self.store.add_interval(112,118,state)
        self.assertEqual(len(self.store.query(0,200)),2)
    def test_manual_overrides_without_double_count(self):
        s={'app':'chrome.exe','title':'','domain':'','category':'浏览器'}
        self.store.add_interval(0,10,s)
        self.store.add_interval(10,20,s)
        self.store.add_manual(5,15,'学习','上课')
        merged=m.reconcile(self.store.query(0,20),0,20)
        grouped={}
        for item in merged:grouped[item['category']]=grouped.get(item['category'],0)+item['end']-item['start']
        self.assertEqual(grouped,{'浏览器':10.0,'学习':10.0})
        self.assertEqual(sum(grouped.values()),20)
    def test_privacy_rules_and_settings(self):
        self.assertEqual(self.store.classify('code.exe','',''),'编程')
        self.store.add_rule('youtube','娱乐')
        self.assertEqual(self.store.classify('chrome.exe','','youtube.com'),'娱乐')
        self.store.update_settings({'record_titles':True,'excluded_apps':['keepass.exe']})
        self.assertEqual(json.loads(self.store.settings_path.read_text(encoding='utf-8'))['record_titles'],True)
    def test_invalid_manual(self):
        with self.assertRaises(ValueError):self.store.add_manual(9,4,'休息','invalid')
        with self.assertRaises(ValueError):self.store.add_manual(0,200000,'休息','invalid')
    def test_midnight(self):
        a,b=m.day_bounds('2026-10-11')
        self.assertGreater(b,a)
        self.assertEqual(dt.datetime.fromtimestamp(a).hour,0)

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        m.STORE=m.Store(Path(self.tmp.name)/'test.sqlite3')
        m.TRACKER=m.Tracker(m.STORE)
        self.http=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
        self.prev_port=m.PORT
        m.PORT=self.http.server_address[1]
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{m.PORT}'
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)
        m.STORE.db.close();self.tmp.cleanup();m.PORT=self.prev_port
    def req(self,path,method='GET',data=None,key=True):
        headers={}
        if key:headers['X-DayTrace-Key']=m.STORE.token
        if data is not None:headers['Content-Type']='application/json'
        body=json.dumps(data).encode() if data is not None else None
        with urlopen(Request(self.base+path,data=body,method=method,headers=headers),timeout=3) as r:
            return r.status,r.read(),r.headers
    def test_manual_endpoints(self):
        start='2026-10-11T10:00';end='2026-10-11T11:30'
        status,data,_=self.req('/api/manual','POST',{'start':start,'end':end,'category':'学习','note':'离散数学'})
        self.assertEqual(status,200)
        rid=json.loads(data)['id']
        _,out,_=self.req('/api/day?date=2026-10-11')
        rec=json.loads(out)
        self.assertEqual(rec['total'],5400)
        self.assertEqual(rec['records'][0]['note'],'离散数学')
        status,_,_=self.req(f'/api/record/{rid}','DELETE')
        self.assertEqual(status,200)
        _,out,_=self.req('/api/day?date=2026-10-11')
        self.assertEqual(json.loads(out)['total'],0)
    def test_settings_and_csv(self):
        _,out,_=self.req('/api/settings','POST',{'record_titles':True})
        self.assertTrue(json.loads(out)['settings']['record_titles'])
        status,out,headers=self.req('/api/export?from=2026-10-11&to=2026-10-11')
        self.assertEqual(status,200)
        self.assertIn('text/csv',headers['Content-Type'])
        self.assertIn('开始',out.decode('utf-8-sig'))
    def test_auth(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as cm:
            self.req('/api/settings','POST',{'paused':True},key=False)
        self.assertEqual(cm.exception.code,403)
    def test_browser_enabled(self):
        self.req('/api/settings','POST',{'browser_enabled':True})
        status,out,_=self.req('/api/browser','POST',{'domain':'example.com'})
        self.assertEqual(status,200)
        self.assertEqual(m.TRACKER.browser['domain'],'example.com')
        self.assertFalse(m.TRACKER.set_browser('https://secret.example/private'))

if __name__=='__main__':unittest.main()
